"""Read-only campus benchmark. Default runs DEV ONLY; test needs explicit opt-in.

Outputs scores from source plus offline replay grids, without changing DB/config.
"""
import argparse
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent


def score(gold,actual):
    g,a=set(gold),set(actual); hits=len(g&a)
    return {'recall':hits/len(g) if g else None,
            'precision':hits/len(a) if g and a else (0.0 if g else None),
            'exact':g==a,'all_hit':g<=a if g else None,
            'negative_fp':bool(a) if not g else None,
            'missing':sorted(g-a),'extra':sorted(a-g)}


def mean(values):
    values=[v for v in values if v is not None]
    return sum(values)/len(values) if values else None


def summarize(rows):
    return {'count':len(rows),'positive_count':sum(bool(r['expected']) for r in rows),
            'negative_count':sum(not r['expected'] for r in rows),
            **{k:mean([r[k] for r in rows]) for k in ['recall','precision','exact','all_hit','negative_fp']},
            'multi_all_hit':mean([r['all_hit'] for r in rows if len(r['expected'])>1]),
            'avg_returned':mean([len(r['actual']) for r in rows])}


def predict(rank,lexical,threshold,k):
    # Apply LIMIT to record candidates, not distinct terms; equality is excluded.
    selected=[r for r in rank if r['similarity']>threshold]
    if len(selected)>k and selected[k-1]['similarity']==selected[k]['similarity']:
        raise ValueError('Top K boundary tie: source SQL does not define tie order')
    return sorted(set(lexical)|{r['parent_word'] for r in selected[:k]})


def validate(data,corpus):
    names={r['word'] for r in corpus['rows'] if r['pid'] is None}
    assert len(names)==26 and len(corpus['rows'])==54
    groups={};ids=set();questions=set()
    for c in data['cases']:
        assert c['id'] not in ids and c['question'] not in questions
        ids.add(c['id']);questions.add(c['question'])
        assert set(c['expected'])<=names and len(c['expected'])==len(set(c['expected']))
        assert c['split'] in ('dev','validation','test')
        groups.setdefault(c['group'],set()).add(c['split'])
    assert all(len(x)==1 for x in groups.values())
    assert len(ids)==200


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--split',choices=['dev','validation','test'],default='dev')
    p.add_argument('--allow-test',action='store_true')
    p.add_argument('--check',action='store_true')
    p.add_argument('--threshold',type=float,help='Fixed candidate; disables grid (required for test)')
    p.add_argument('--top-k',type=int,help='Fixed candidate; disables grid (required for test)')
    p.add_argument('--strategy',choices=['hybrid','parents_only','description'],default='hybrid')
    args=p.parse_args()
    data=json.loads((HERE/'questions.json').read_text(encoding='utf-8'))
    snapshot=json.loads((HERE/'corpus.json').read_text(encoding='utf-8'))
    validate(data,snapshot)
    if args.check:
        assert score(['a'],['a','b'])['precision']==.5
        assert score([],['b'])['precision'] is None
        assert score(['a'],[])['precision']==0
        rank=[{'parent_word':'a','similarity':.8},{'parent_word':'a','similarity':.7},{'parent_word':'b','similarity':.6}]
        assert predict(rank,['c'],.4,2)==['a','c']
        assert predict(rank,[],.8,2)==[]
        print('PASS: 200 cases; groups disjoint; scoring and alias LIMIT checks. No model/DB tests run.')
        return
    if (args.threshold is None)!=(args.top_k is None):
        p.error('threshold and top-k must be specified together')
    if args.threshold is not None and (not math.isfinite(args.threshold) or not -1<=args.threshold<=1 or args.top_k<1):
        p.error('Invalid threshold/top-k')
    if args.split=='test' and (not args.allow_test or args.threshold is None):
        p.error('Test requires --allow-test and fixed --threshold --top-k; no test grid allowed')
    root=HERE.parents[2]
    os.chdir(root/'backend');sys.path.insert(0,str(root/'backend'))
    import numpy as np
    from sqlalchemy import text,event
    from sqlmodel import Session
    from common.core.config import settings
    from common.core.db import engine
    from apps.ai_model.embedding import EmbeddingModelCache,local_embedding_model
    from apps.terminology.curd import terminology as source
    if not settings.EMBEDDING_ENABLED:raise RuntimeError('EMBEDDING_ENABLED must be true')
    def readonly(c):c.exec_driver_sql('SET TRANSACTION READ ONLY')
    event.listen(engine,'begin',readonly)
    observations=[]
    selected=[c for c in data['cases'] if c['split']==args.split]
    fields=['id','oid','pid','word','description','enabled','specific_ds','datasource_ids','has_vector','dimensions']
    config={'threshold':settings.EMBEDDING_TERMINOLOGY_SIMILARITY,'top_k':settings.EMBEDDING_TERMINOLOGY_TOP_COUNT,
            'model_path':local_embedding_model.name,'oid':snapshot['oid'],
            'source_sha256':hashlib.sha256(Path(source.__file__).read_bytes()).hexdigest(),
            'questions_sha256':hashlib.sha256((HERE/'questions.json').read_bytes()).hexdigest(),
            'snapshot_sha256':hashlib.sha256((HERE/'corpus.json').read_bytes()).hexdigest(),
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    scope='oid=:oid AND enabled=true AND (specific_ds=false OR specific_ds IS NULL)'
    try:
        with Session(engine) as session:
            live=[dict(r) for r in session.execute(text('SELECT id,oid,pid,word,description,enabled,specific_ds,datasource_ids,embedding IS NOT NULL AS has_vector,vector_dims(embedding) AS dimensions FROM terminology WHERE oid=:oid ORDER BY id'),{'oid':snapshot['oid']}).mappings()]
            if live!=snapshot['rows']:raise RuntimeError('Actual corpus differs from frozen snapshot. Stop and review changes.')
            if not all(r['enabled'] and not r['specific_ds'] and r['has_vector'] and r['dimensions']==768 for r in live):raise RuntimeError('Invalid scope/embedding state')
            roots={r['id']:r for r in live if r['pid'] is None}
            owner={r['word']:roots[r['pid'] if r['pid'] is not None else r['id']]['word'] for r in live}
            model=EmbeddingModelCache.get_model()
            # Experimental document vectors stay in memory; source branch uses DB vectors.
            texts=[r['word']+'。'+roots[r['pid'] if r['pid'] is not None else r['id']]['description'] for r in live]
            documents=np.asarray(model.embed_documents(texts),dtype=float)
            norms=np.linalg.norm(documents,axis=1)
            if not np.isfinite(documents).all() or (norms==0).any():raise RuntimeError('Invalid document embeddings')
            documents=documents/norms[:,None]
            for n,case in enumerate(selected,1):
                q=case['question']; stderr=io.StringIO();start=time.perf_counter()
                with contextlib.redirect_stderr(stderr):
                    result=source.select_terminology_by_word(session,q,oid=snapshot['oid'])
                elapsed=(time.perf_counter()-start)*1000
                if 'Traceback (most recent call last)' in stderr.getvalue():raise RuntimeError(stderr.getvalue())
                actual=sorted({owner[w] for group in result for w in group['words']})
                v=model.embed_query(q)
                rank=[dict(r) for r in session.execute(text('SELECT id,pid,word,1-(embedding <=> :v) AS similarity FROM terminology WHERE '+scope+' AND embedding IS NOT NULL ORDER BY similarity DESC,id'),{'oid':snapshot['oid'],'v':str(v)}).mappings()]
                for r in rank:r.update(parent_word=owner[r['word']],similarity=float(r['similarity']))
                lexical_rows=[dict(r) for r in session.execute(text("SELECT id,pid,word FROM terminology WHERE "+scope+" AND :q ILIKE '%' || word || '%'"),{'oid':snapshot['oid'],'q':q}).mappings()]
                lexical=sorted({owner[r['word']] for r in lexical_rows})
                if predict(rank,lexical,config['threshold'],config['top_k'])!=actual:
                    raise RuntimeError('Source replay mismatch: '+case['id'])
                query=np.asarray(v,dtype=float);norm=np.linalg.norm(query)
                if norm==0 or not np.isfinite(query).all():raise RuntimeError('Invalid query embedding')
                sims=documents@(query/norm)
                descrank=sorted([{'id':r['id'],'pid':r['pid'],'word':r['word'],'parent_word':owner[r['word']],'similarity':float(sims[i])} for i,r in enumerate(live)],key=lambda r:(-r['similarity'],r['id']))
                observations.append({**case,'actual':actual,'lexical_actual':lexical,'lexical_rows':lexical_rows,
                    'source_ms':round(elapsed,3),'ranking':rank,'description_ranking':descrank,
                    'parent_lexical':sorted({owner[r['word']] for r in lexical_rows if r['pid'] is None}),**score(case['expected'],actual)})
                print(f'[{n}/{len(selected)}] {case["id"]} expected={len(case["expected"])} actual={len(actual)}',flush=True)
    finally:
        event.remove(engine,'begin',readonly);engine.dispose()
    runs=[]
    # No test grids. Validation may compare prespecified grid; test fixed candidate only.
    combinations=[(args.strategy,args.threshold,args.top_k)] if args.threshold is not None else [
        (s,t,k) for s in ['hybrid','parents_only','description'] for t in [.4,.45,.5,.55,.6,.65] for k in [1,3,5,8,10]]
    def evaluate(strategy,t,k):
        rows=[]
        for c in observations:
            rank=c['description_ranking'] if strategy=='description' else c['ranking']
            lexical=c['lexical_actual']
            if strategy=='parents_only':
                rank=[r for r in rank if r['pid'] is None];lexical=c['parent_lexical']
            actual=lexical if strategy=='lexical' else predict(rank,lexical,t,k)
            rows.append({**{key:c[key] for key in ['id','category','expected']},'actual':actual,**score(c['expected'],actual)})
        return {'strategy':strategy,'threshold':t,'top_k':k,'summary':summarize(rows),
                'by_category':{cat:summarize([r for r in rows if r['category']==cat]) for cat in ['single','multi','negative']},'cases':rows}
    runs.append(evaluate('lexical',config['threshold'],config['top_k']))
    for s,t,k in combinations:runs.append(evaluate(s,t,k))
    # Full observations allow alias attribution and inspect substring collisions.
    report={'split':args.split,'created_at':datetime.now(timezone.utc).isoformat(),'config':config,
        'label_status':data['label_status'],'label_policy':data['label_policy'],'split_policy':data['split_policy'],
        'source_summary':summarize(observations),'corpus':live,'description_texts':texts,'observations':observations,'runs':runs,
        'notes':['Synthetic benchmark; labels need domain review. Not real-user accuracy.',
        'Precision averages positives only. Negatives evaluated separately. Not comparable to old mixed-denominator precision.',
        'parents_only removes aliases from BOTH lexical and vector branches; not a per-alias causal estimate.',
        'description keeps all records and lexical branch, uses in-memory cosine, no DB writes.',
        'Timing includes source embedding but excludes experimental scoring; not a load benchmark.']}
    out=HERE/'reports'/(args.split+'-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'));out.mkdir(parents=True)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=report['source_summary']
    lines=['# 校园术语批量评测', '',f'数据分组：{args.split}。合成题；需业务复核标签。',
           '精确率仅统计正例；所有结果为主术语集合。最具体术语严格评分，多返回上级术语不自动等于业务有害。',
           '', '## 原版源码基线','', '```json',json.dumps(summary,ensure_ascii=False,indent=2),'```','',
           '## 同参数对照','', '|方案|阈值|Top K|召回率|正例精确率|完全匹配|多术语全命中|负例误召回|', '|---|---|---|---|---|---|---|---|']
    def percent(x):return 'n/a' if x is None else f'{x*100:.1f}%'
    displayed = ([('hybrid',config['threshold'],config['top_k']),
                  (args.strategy,args.threshold,args.top_k)] if args.threshold is not None
                 else [(s,config['threshold'],config['top_k']) for s in ['lexical','hybrid','parents_only','description']])
    if args.threshold is not None:
        lines[lines.index('## 同参数对照')] = '## 原版基线与固定候选（参数分别列示）'
    for s,t,k in displayed:
        r=evaluate(s,t,k);m=r['summary']
        lines.append('|'+ '|'.join([s,str(t),str(k),*[percent(m[x]) for x in ['recall','precision','exact','multi_all_hit','negative_fp']]])+'|')
    lines += ['', '完整参数网格、逐题错误、词语命中记录及向量排名见report.json。不要根据开发集最高分直接修改服务。']
    (out/'summary.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n'+'\n'.join(lines));print('\n报告目录：',out)


if __name__=='__main__':main()
