"""Local embedding length/latency experiment; no DB writes or network calls.

inspect: tokenizer lengths only (loads local model).
bench: CPU warm inference with document-only caps; question length unchanged.
Not a PostgreSQL retrieval, concurrency or end-to-end response benchmark.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from datetime import datetime

HERE = Path(__file__).resolve().parent


def percentile(values, fraction):
    xs = sorted(values)
    if not xs:
        return None
    pos = (len(xs)-1)*fraction
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi]-xs[lo])*(pos-lo)


def stats(values):
    return {'count': len(values), 'mean': statistics.mean(values) if values else None,
            'p50': percentile(values,.5), 'p95': percentile(values,.95),
            'min': min(values) if values else None, 'max': max(values) if values else None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['inspect','bench'], default='inspect')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--rounds',type=int,default=5)
    parser.add_argument('--batch-size',type=int,default=8)
    args=parser.parse_args()
    if args.check:
        assert percentile([1,2,3,4,5],.5)==3
        assert abs(percentile([1,2,3,4,5],.95)-4.8)<1e-8
        assert stats([])['p95'] is None
        print('PASS: statistics helpers; no model measurement performed.')
        return
    if args.rounds<3 or args.batch_size<1:
        parser.error('rounds must be >=3; batch-size must be >=1')
    root=HERE.parents[2]
    os.chdir(root/'backend');sys.path.insert(0,str(root/'backend'))
    # Set before importing model libraries. Do not download missing files.
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    from common.core.config import settings
    import numpy as np
    import torch
    import sentence_transformers
    from sentence_transformers import SentenceTransformer
    import platform

    corpus_path=HERE/'corpus.json'
    corpus=json.loads(corpus_path.read_text(encoding='utf-8'))
    records=corpus['rows']
    roots={r['id']:r for r in records if r['pid'] is None}
    texts=[r['word']+'。'+roots[r['pid'] if r['pid'] is not None else r['id']]['description'] for r in records]
    names=[r['word'] for r in records]
    model_path=Path(settings.LOCAL_MODEL_PATH)/'embedding'/'shibing624_text2vec-base-chinese'
    if not model_path.is_dir():raise RuntimeError('Local model folder missing: '+str(model_path))
    started=time.perf_counter()
    model=SentenceTransformer(str(model_path),device='cpu',local_files_only=True)
    load_ms=(time.perf_counter()-started)*1000
    original_limit=int(model.max_seq_length)
    # Includes special tokens, no truncation; no assumptions about chars/token.
    lengths=[len(model.tokenizer(t,add_special_tokens=True,truncation=False)['input_ids']) for t in texts]
    name_lengths=[len(model.tokenizer(t,add_special_tokens=True,truncation=False)['input_ids']) for t in names]
    candidates=sorted({min(cap,original_limit) for cap in [64,128,256]}|{original_limit})
    entries=[{'id':r['id'],'word':r['word'],'text':t,'tokens':n,
              'exceeds_model_limit':n>original_limit} for r,t,n in zip(records,texts,lengths)]
    metadata={'mode':args.mode,'intended_threshold':.65,'intended_top_k':3,
              'note':'Threshold/Top K are recorded only; this script does not retrieve or score recall.',
              'corpus_source':'Frozen corpus.json, not a fresh DB read',
              'corpus_sha256':hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
              'model_path':str(model_path),'model_limit_tokens':original_limit,
              'tokenizer_model_max_length':model.tokenizer.model_max_length,
              'load_ms':load_ms,'load_note':'Constructor wall time only, excludes Python/import startup; OS file cache may already be warm.',
              'python':sys.version,'platform':platform.platform(),'torch':torch.__version__,
              'sentence_transformers':sentence_transformers.__version__,
              'torch_threads':torch.get_num_threads(),'device':'cpu','batch_size':args.batch_size,
              'normalization':True,'name_token_stats':stats(name_lengths),'description_token_stats':stats(lengths),
              'candidate_caps':[{'tokens':cap,'would_truncate':sum(n>cap for n in lengths)} for cap in candidates]}
    print('\n模型有效上限（tokens）:',original_limit)
    print('名称文本长度:',json.dumps(stats(name_lengths),ensure_ascii=False))
    print('名称＋解释长度:',json.dumps(stats(lengths),ensure_ascii=False))
    print('注意：长度包括特殊token；截断可能丢失关键业务规则。')
    for item in metadata['candidate_caps']:print('长度上限:',item['tokens'],'会截断:',item['would_truncate'],'/',len(texts))
    print('\n最长的5条:')
    for r in sorted(entries,key=lambda x:x['tokens'],reverse=True)[:5]:print(r['word'],r['tokens'],'tokens')
    report={'metadata':metadata,'entries':entries}
    if args.mode=='bench':
        questions=json.loads((HERE/'questions.json').read_text(encoding='utf-8'))
        # Fixed development probes only. Do not consume held-out test questions.
        ids=['T01-3','T03-3','T05-3','T07-3','T08-3','T10-3','T15-3','T16-3','M01-1','M02-1','N01-1','N03-1']
        probes=[c for c in questions['cases'] if c['id'] in ids and c['split']=='dev']
        assert len(probes)==12
        def encode(batch):
            result=model.encode(batch,batch_size=args.batch_size,normalize_embeddings=True,
                                convert_to_numpy=True,show_progress_bar=False)
            if not np.isfinite(result).all():raise RuntimeError('Invalid embeddings')
            return result
        # First inference separately; all following steady measurements warmed.
        started=time.perf_counter();encode([probes[0]['question']]);report['first_inference_ms']=(time.perf_counter()-started)*1000
        encode([p['question'] for p in probes])
        configurations=[('name_only',original_limit,names),('description_full',original_limit,texts)]
        configurations += [(f'description_cap_{cap}',cap,texts) for cap in candidates if cap<original_limit]
        samples={label:[] for label,_,_ in configurations}; vectors={}
        for label,cap,batch in configurations:
            model.max_seq_length=cap;encode(batch[:args.batch_size])
        try:
            for round_id in range(args.rounds):
                # Rotate ordering to reduce systematic warmup/thermal ordering bias.
                order=configurations[round_id%len(configurations):]+configurations[:round_id%len(configurations)]
                for label,cap,batch in order:
                    model.max_seq_length=cap
                    started=time.perf_counter();v=encode(batch);elapsed=(time.perf_counter()-started)*1000
                    samples[label].append(elapsed);vectors[label]=v
                    print(f'文档轮次 {round_id+1}/{args.rounds} {label}: {elapsed:.1f} ms',flush=True)
        finally:
            model.max_seq_length=original_limit
        results=[]
        full=vectors['description_full']
        for label,cap,batch in configurations:
            elapsed=samples[label]
            result={'scenario':label,'limit':cap,'total_batch_ms':stats(elapsed),
                    'records_per_second':len(batch)*len(elapsed)*1000/sum(elapsed),
                    'amortized_ms_per_record':sum(elapsed)/(len(elapsed)*len(batch)),
                    'would_truncate':sum(n>cap for n in (name_lengths if label=='name_only' else lengths))}
            if label.startswith('description'):
                result['cosine_to_full_stats']=stats(np.sum(vectors[label]*full,axis=1).tolist())
            results.append(result)
        query_samples=[]
        for rep in range(args.rounds):
            for probe in probes:
                started=time.perf_counter();encode([probe['question']]);elapsed=(time.perf_counter()-started)*1000
                query_samples.append({'id':probe['id'],'ms':elapsed})
        report.update(document_benchmarks=results,query_samples=query_samples,
                      query_latency_ms=stats([r['ms'] for r in query_samples]),
                      query_probes=probes,rounds=args.rounds)
        print('\n文档批量编码（每轮54条；均摊耗时不是单请求P95）：')
        for r in results:print(r['scenario'],'批次P50毫秒',round(r['total_batch_ms']['p50'],2),
            '每秒条数',round(r['records_per_second'],2),'截断条数',r['would_truncate'])
        print('\n问题编码耗时（毫秒）：',json.dumps(report['query_latency_ms'],ensure_ascii=False))
        print('只测embedding，不含PostgreSQL、网络、提示词、大模型生成或并发排队。')
    out=HERE/'reports'/('embedding-'+args.mode+'-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    out.mkdir(parents=True)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('\n报告目录：',out)


if __name__=='__main__':main()
