"""Read-only 26-term baseline plus offline parameter/alias diagnostics.
Requires adjacent campus_dev_v1.json and frozen snapshot. --check is offline.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent


def score(gold, actual):
    gold,actual=set(gold),set(actual)
    tp=len(gold & actual)
    return dict(recall=tp/len(gold) if gold else None,
        precision=tp/len(actual) if gold and actual else (0.0 if gold else None),
        all_hit=gold<=actual if gold else None, exact=gold==actual,
        false_positive=bool(actual) if not gold else None,
        missing=sorted(gold-actual), extra=sorted(actual-gold))


def summary(rows):
    out={'count':len(rows),'positive_count':sum(bool(r['expected']) for r in rows)}
    for key in ['recall','precision','all_hit','exact','false_positive']:
        values=[r[key] for r in rows if r[key] is not None]
        out[key]=sum(values)/len(values) if values else None
    multis=[r['all_hit'] for r in rows if len(r['expected'])>1]
    out['multi_all_hit']=sum(multis)/len(multis) if multis else None
    return out


def retrieve(case, threshold, k, method='hybrid'):
    rank=sorted(case['ranking'],key=lambda r:(-r['similarity'],r['id']))
    lexical=case['lexical']
    if method=='parents_only':
        rank=[r for r in rank if r['pid'] is None]
        lexical=[r for r in lexical if r['pid'] is None]
    if method=='lexical':
        return sorted({r['parent_word'] for r in lexical})
    eligible=[r for r in rank if r['similarity']>threshold]
    if len(eligible)>k and eligible[k-1]['similarity']==eligible[k]['similarity']:
        raise ValueError('Top K边界同分，原SQL无稳定排序，停止回放')
    actual={r['parent_word'] for r in eligible[:k]}
    if method!='vector_only':actual.update(r['parent_word'] for r in lexical)
    return sorted(actual)


def evaluate(cases,threshold,k,method):
    result=[]
    for c in cases:
        actual=retrieve(c,threshold,k,method)
        result.append({**{x:c[x] for x in ['id','category','question','expected']},
                       'actual':actual,**score(c['expected'],actual)})
    return result


def validate(data,snapshot):
    names={r['word'] for r in snapshot['rows'] if r['pid'] is None}
    cases=data['cases']
    assert len(cases)==120 and len({c['id'] for c in cases})==120
    for c in cases:
        assert c['split']=='dev' and c['question'].strip()
        assert set(c['expected'])<=names
        assert len(c['expected'])==len(set(c['expected']))
    # Negative examples must not increase positive-precision denominator.
    assert score([],['a'])['precision'] is None
    assert score(['a'],[])['precision']==0
    assert score(['a','b'],['a','c'])['recall']==.5
    test={'lexical':[{'pid':None,'parent_word':'L'}], 'ranking':[
        {'id':1,'pid':None,'parent_word':'A','similarity':.8},
        {'id':2,'pid':1,'parent_word':'A','similarity':.7},
        {'id':3,'pid':None,'parent_word':'B','similarity':.6}]}
    assert retrieve(test,.4,2)==['A','L']
    assert retrieve(test,.8,2)==['L']
    assert retrieve(test,.4,2,'parents_only')==['A','B','L']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    fixture_path=HERE/'campus_dev_v1.json'
    fixture=json.loads(fixture_path.read_text(encoding='utf-8'))
    snapshot_path=HERE/'snapshots'/fixture['snapshot']
    snapshot=json.loads(snapshot_path.read_text(encoding='utf-8'))
    validate(fixture,snapshot)
    if args.check:
        print('检查通过：120题、26术语映射、评分口径与回放边界；未连接数据库。')
        return
    root=HERE.parents[1]
    os.chdir(root/'backend')
    sys.path.insert(0,str(root/'backend'))
    from sqlalchemy import event,text
    from sqlmodel import Session
    from common.core.config import settings
    from common.core.db import engine
    from apps.ai_model.embedding import EmbeddingModelCache,local_embedding_model
    from apps.terminology.curd import terminology as retriever
    if not settings.EMBEDDING_ENABLED:raise ValueError('向量检索必须启用')
    def readonly(connection):connection.exec_driver_sql('SET TRANSACTION READ ONLY')
    event.listen(engine,'begin',readonly)
    oid=snapshot['oid']
    t=settings.EMBEDDING_TERMINOLOGY_SIMILARITY
    k=settings.EMBEDDING_TERMINOLOGY_TOP_COUNT
    runs=[]
    try:
        with Session(engine) as session:
            session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
            fields='id,oid,pid,word,description,enabled,specific_ds,datasource_ids,embedding IS NOT NULL AS has_vector,vector_dims(embedding) AS dimensions'
            corpus=[dict(r) for r in session.execute(text('SELECT '+fields+' FROM terminology WHERE oid=:oid ORDER BY id'),{'oid':oid}).mappings()]
            if corpus!=snapshot['rows']:raise ValueError('数据库术语与快照不一致，请重新确认语料，不能混用版本。')
            if not all(r['enabled'] and not r['specific_ds'] and r['has_vector'] and r['dimensions']==768 for r in corpus):
                raise ValueError('需要启用、全局生效且向量齐全的快照')
            parents={r['id']:r['word'] for r in corpus if r['pid'] is None}
            parent_of={r['id']:parents[r['pid'] if r['pid'] is not None else r['id']] for r in corpus}
            model=EmbeddingModelCache.get_model()
            model.embed_query('初始化')
            print('语料一致：',len(parents),'个主术语，',len(corpus),'条向量。阈值:',t,'Top K:',k,flush=True)
            scope='oid=:oid AND enabled=true AND (specific_ds=false OR specific_ds IS NULL)'
            for index,c in enumerate(fixture['cases'],1):
                started=time.perf_counter()
                stderr=io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result=retriever.select_terminology_by_word(session,c['question'],oid=oid)
                if 'Traceback (most recent call last)' in stderr.getvalue():
                    raise RuntimeError('源码吞掉异常，停止评测：'+c['id']+'\n'+stderr.getvalue())
                ms=(time.perf_counter()-started)*1000
                actual=set()
                for group in result:
                    match=set(group['words']) & set(parents.values())
                    if len(match)!=1:raise ValueError('主术语映射异常')
                    actual.update(match)
                vector=model.embed_query(c['question'])
                ranked=[dict(r) for r in session.execute(text(
                    'SELECT id,pid,word,1-(embedding <=> :v) AS similarity FROM terminology WHERE '+scope+
                    ' AND embedding IS NOT NULL ORDER BY similarity DESC,id ASC'),{'oid':oid,'v':str(vector)}).mappings()]
                literal=[dict(r) for r in session.execute(text(
                    "SELECT id,pid,word FROM terminology WHERE "+scope+" AND :q ILIKE '%' || word || '%'"),{'oid':oid,'q':c['question']}).mappings()]
                for r in ranked+literal:r['parent_word']=parent_of[r['id']]
                for r in ranked:
                    r['similarity']=float(r['similarity'])
                    if not math.isfinite(r['similarity']):raise ValueError('相似度异常')
                record={**c,'actual':sorted(actual),'ranking':ranked,'lexical':literal,
                        'source_ms':round(ms,2),**score(c['expected'],actual)}
                if retrieve(record,t,k)!=sorted(actual):raise ValueError('源码与回放不一致：'+c['id'])
                runs.append(record)
                if index%10==0:print(f'已完成 {index}/120，源码与回放一致。',flush=True)
    finally:
        event.remove(engine,'begin',readonly)
        engine.dispose()
    output=HERE/'reports'/datetime.now().strftime('campus-dev-%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'fixture':fixture,
            'snapshot':snapshot,'config':{'threshold':t,'top_k':k,'model_path':local_embedding_model.name,
            'source_sha256':hashlib.sha256(Path(retriever.__file__).read_bytes()).hexdigest(),
            'fixture_sha256':hashlib.sha256(fixture_path.read_bytes()).hexdigest()},
            'metric_policy':'precision/recall仅正例宏平均，正例空返回记0；误召回率仅负例；exact全部题。',
            'summary':summary(runs),'by_category':{cat:summary([r for r in runs if r['category']==cat]) for cat in sorted({r['category'] for r in runs})},'cases':runs}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    methods={m:summary(evaluate(runs,t,k,m)) for m in ['lexical','vector_only','hybrid','parents_only']}
    grids=[]
    for threshold in sorted({t,.4,.45,.5,.55,.6,.65,.7}):
        for count in sorted({k,1,3,5,8,10}):
            evaluated=evaluate(runs,threshold,count,'hybrid')
            grids.append({'threshold':threshold,'top_k':count,**summary(evaluated)})
    (output/'parameter_scan.json').write_text(json.dumps(grids,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'method_comparison.json').write_text(json.dumps(methods,ensure_ascii=False,indent=2),encoding='utf-8')
    diagnostics=[]
    for name in parents.values():
        diagnostics.append({'term':name,'gold_questions':sum(name in r['expected'] for r in runs),
            'missed_questions':[r['id'] for r in runs if name in r['missing']],
            'extra_questions':[r['id'] for r in runs if name in r['extra']],
            'lexical_extra_questions':[r['id'] for r in runs if name not in r['expected'] and any(x['parent_word']==name for x in r['lexical'])]})
    (output/'term_diagnostics.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 校园120题开发集基线','',fixture['limitations'],'',report['metric_policy'],'',
        '当前阶段尚未实验解释向量化，也未使用独立验证/测试集。参数扫描和无同义词方案均为离线回放。',
        '', '```json',json.dumps(report['summary'],ensure_ascii=False,indent=2),'```','',
        '|题号|题型|问题|预期|实际|漏召回|多召回|','|---|---|---|---|---|---|---|']
    for r in runs:lines.append('|'+ '|'.join([r['id'],r['category'],r['question'],','.join(r['expected']) or '空',','.join(r['actual']) or '空',','.join(r['missing']),','.join(r['extra'])])+'|')
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n原版汇总：\n'+json.dumps(report['summary'],ensure_ascii=False,indent=2))
    print('\n分题型：\n'+json.dumps(report['by_category'],ensure_ascii=False,indent=2))
    print('\n四种方法对照：\n'+json.dumps(methods,ensure_ascii=False,indent=2))
    print('\n报告目录：',output)
    print('请把此目录复制回D盘项目的tools/campus_recall/results目录，供下一步分析。')


if __name__=='__main__':main()
