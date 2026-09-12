"""Actual SQL-example source baseline plus read-only diagnostic replay."""
import contextlib,hashlib,io,json,os,sys,time,math
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
from metrics import score,summarize
root=Path('/home/sqlbotdev/sqlbot-local/backend')
os.chdir(root);sys.path.insert(0,str(root))
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
from sqlalchemy import text,event
from sqlmodel import Session
from common.core.db import engine
from common.core.config import settings
from apps.ai_model.embedding import EmbeddingModelCache,local_embedding_model
from apps.data_training.curd import data_training as source
fixture=json.loads((HERE/'corpus.json').read_text(encoding='utf-8'))
data=json.loads((HERE/'questions.json').read_text(encoding='utf-8'))
frozen=json.loads((HERE/'live_snapshot.json').read_text(encoding='utf-8'))
out=HERE/'reports'/datetime.now().strftime('%Y%m%d-%H%M%S');out.mkdir(parents=True)
def readonly(c):c.exec_driver_sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
event.listen(engine,'begin',readonly)
observations=[];checks=[]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
config=dict(threshold=settings.EMBEDDING_DATA_TRAINING_SIMILARITY,top_k=settings.EMBEDDING_DATA_TRAINING_TOP_COUNT,
            model_path=local_embedding_model.name,source_sha256=sha(source.__file__),questions_sha256=sha(HERE/'questions.json'),
            corpus_sha256=sha(HERE/'corpus.json'),snapshot_sha256=sha(HERE/'live_snapshot.json'),script_sha256=sha(__file__))
try:
 with Session(engine) as session:
    live=[dict(r) for r in session.execute(text('SELECT id,oid,datasource,advanced_application,question,description,enabled,embedding::text AS vector FROM data_training ORDER BY id')).mappings()]
    if len(live)!=200 or not settings.EMBEDDING_ENABLED:raise ValueError('Expected exactly 200 records and embedding enabled')
    for row,old in zip(live,frozen['rows']):
        v=json.loads(row['vector']) if row['vector'] else None
        if not v or len(v)!=768 or not all(math.isfinite(x) for x in v) or sum(x*x for x in v)==0:raise ValueError('Bad vector')
        if any(row[k]!=old[k] for k in ['id','oid','datasource','advanced_application','question','description','enabled']) or hashlib.sha256(json.dumps(v).encode()).hexdigest()!=old['vector_sha256']:raise ValueError('Snapshot changed')
    byq={r['question']:r for r in live}
    if len(byq)!=200 or any(not r['enabled'] for r in live):raise ValueError('Duplicate questions or disabled records')
    idmap={byq[r['question']]['id']:r['example_id'] for r in fixture['rows']}
    scopes={(r['oid'],r['datasource'],r['advanced_application']) for r in live}
    if len(scopes)!=1:raise ValueError('Multiple scopes require separate benchmarks')
    oid,ds,app=next(iter(scopes));config.update(oid=oid,datasource=ds,advanced_application=app)
    if ds is None and app is None:raise ValueError('Unbound records')
    scope='oid=:oid AND enabled=true AND '+('advanced_application=:scopeid' if app is not None else 'datasource=:scopeid')
    params={'oid':oid,'scopeid':app if app is not None else ds}
    model=EmbeddingModelCache.get_model()
    documents=model.embed_documents([r['question'] for r in live])
    alignment=[]
    for r,new in zip(live,documents):
        old=json.loads(r['vector'])
        cosine=sum(a*b for a,b in zip(old,new))/(math.sqrt(sum(x*x for x in old))*math.sqrt(sum(x*x for x in new)))
        alignment.append(dict(id=r['id'],cosine=cosine))
    config['stored_vector_alignment_min']=min(r['cosine'] for r in alignment)
    config['stored_vector_alignment_below_0999']=sum(r['cosine']<.999 for r in alignment)
    if config['stored_vector_alignment_below_0999']:raise ValueError('Stored embeddings do not match current question/model')
    def call(q):
        err=io.StringIO();start=time.perf_counter()
        with contextlib.redirect_stderr(err):
            result=source.select_training_by_question(session,q,oid=oid,datasource=ds,advanced_application_id=app)
        if 'Traceback' in err.getvalue():raise RuntimeError(err.getvalue())
        ids=[]
        for r in result:
            record=byq[r['question']]
            if r['suggestion-answer']!=record['description']:raise ValueError('Source detail mismatch')
            ids.append(idmap[record['id']])
        return sorted(set(ids)),round((time.perf_counter()-start)*1000,3)
    # Check exact queries independently; exclude from scored benchmark.
    for i,r in enumerate(fixture['rows'],1):
        ids,ms=call(r['question']);checks.append(dict(example_id=r['example_id'],self_hit=r['example_id'] in ids,returned=ids,ms=ms))
        if i%50==0: print(f'Self-check {i}/200',flush=True)
    for i,c in enumerate(data['cases'],1):
        q=c['question'];actual,ms=call(q);v=model.embed_query(q)
        if len(v)!=768 or not all(math.isfinite(x) for x in v) or sum(x*x for x in v)==0:raise ValueError('Bad query vector')
        rank=[dict(r) for r in session.execute(text('SELECT id,1-(embedding <=> :v) AS similarity FROM data_training WHERE '+scope+' ORDER BY similarity DESC,id'),{**params,'v':str(v)}).mappings()]
        ranking=[dict(example_id=idmap[r['id']],similarity=float(r['similarity'])) for r in rank]
        lexical=sorted(idmap[r[0]] for r in session.execute(text("SELECT id FROM data_training WHERE "+scope+" AND (:q ILIKE '%' || question || '%' OR question ILIKE '%' || :q || '%')"),{**params,'q':q}))
        eligible=[r for r in ranking if r['similarity']>config['threshold']];k=config['top_k']
        if len(eligible)>k and eligible[k-1]['similarity']==eligible[k]['similarity']:raise ValueError('Top K boundary tie')
        vector=[r['example_id'] for r in eligible[:k]]
        if sorted(set(lexical+vector))!=actual:raise ValueError('Replay mismatch '+c['id'])
        gold=set().union(*(set(g) for g in c['required_groups'])) if c['required_groups'] else set()
        pos=next((j for j,r in enumerate(ranking,1) if r['example_id'] in gold),None)
        observation={**c,'actual':actual,'lexical':lexical,'vector':vector,'ranking':ranking,'source_ms':ms,
                     'vector_gold_rank':pos,'vector_mrr':1/pos if pos else (0 if gold else None),
                     **score(c['required_groups'],actual)}
        observations.append(observation)
        if i%25==0: print(f'Benchmark {i}/296',flush=True)
finally:
 event.remove(engine,'begin',readonly);engine.dispose()
 if observations:
    (out/'partial.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2),encoding='utf-8')
summary=summarize(observations)
comparisons={}
for method in ['lexical','vector','actual']:
 comparisons[method]=summarize([score(c['required_groups'],c[method]) for c in observations])
report=dict(created_at=datetime.now(timezone.utc).isoformat(),stage=data['stage'],label_status=data['label_status'],config=config,
            source_summary=summary,by_category={cat:summarize([c for c in observations if c['category']==cat]) for cat in ['single','multi','negative']},
            negative_by_type={t:summarize([c for c in observations if c.get('negative_type')==t]) for t in ['out_of_domain','missing_data']},
            comparisons=comparisons,self_check=dict(count=len(checks),hit=sum(c['self_hit'] for c in checks),rows=checks),
            placeholder_sql_count=sum('{{' in r['description'] for r in live),observations=observations,
            source_replay_matches=len(observations),id_mapping=idmap)
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['# SQL 示例召回初始基线','',data['label_status'],'',f'实际配置：阈值 {config["threshold"]} / Top K {config["top_k"]}。库200条，768维。',
       f'原问题自检 {report["self_check"]["hit"]}/200；不计入下列分数。源码回放一致 {len(observations)}/296。',
       '','|方案|召回率|正例精确率|完全满足且无额外返回|多意图全命中|负例误召回|','|---|---|---|---|---|---|']
for name,m in comparisons.items():
 lines.append('|'+name+'|'+'|'.join('N/A' if m[k] is None else f'{m[k]*100:.1f}%' for k in ['recall','precision','exact','multi_all_hit','negative_fp'])+'|')
lines+=['','这296题均为初始开发诊断，200道模板改写、48道组合题、48道负例。没有参数扫描或运行配置修改。',
        '200条SQL仍含表名占位符：本轮仅测试问题召回，不证明SQL可执行。',
        '正确ID按查询任务严格标注，相关但不完成同一查询的示例计额外返回；标签需业务复核。','', '## 漏召回与负例误召回（最多20题）','']
bad=[c for c in observations if c['missing_groups'] or c['negative_fp']]
for c in bad[:20]:lines.append(f'- {c["id"]} {c["question"]} 标准={c["required_groups"]} 实际={c["actual"]}')
(out/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'report':str(out),'summary':summary,'comparisons':comparisons},ensure_ascii=False),flush=True)
