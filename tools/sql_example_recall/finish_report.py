import json,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent
out=HERE/'reports/20260910-172517'
d=json.loads((out/'report.json').read_text(encoding='utf-8'))
corpus=json.loads((HERE/'corpus.json').read_text(encoding='utf-8'))
labels={r['example_id']:r['question'] for r in corpus['rows']}
for name in ['questions.json','live_snapshot.json','corpus.json','run_live.py','metrics.py']:
    shutil.copyfile(HERE/name,out/name)
single=[c for c in d['observations'] if c['category']=='single']
multi=[c for c in d['observations'] if c['category']=='multi']
hits={k:sum(c['vector_gold_rank']<=k for c in single)/len(single) for k in [1,3,5,10,20]}
extra={
 'single_vector_hit_at_k':hits,
 'single_vector_mrr':sum(c['vector_mrr'] for c in single)/len(single),
 'single_missed':sum(bool(c['missing_groups']) for c in single),
 'multi_full':sum(c['all_hit'] for c in multi),
 'multi_partial':sum(c['recall']==.5 for c in multi),
 'multi_none':sum(c['recall']==0 for c in multi),
}
(out/'diagnostics.json').write_text(json.dumps(extra,ensure_ascii=False,indent=2),encoding='utf-8')
lines=[
'# SQL 示例召回测试报告','',
'本轮为初始开发基线，使用实际 `select_training_by_question()`，未修改数据库、向量或运行参数。',
'','## 数据与验证','',
'- 示例200条，全部启用，绑定「学生成绩」，向量768维，异常0条。',
f'- 重新编码核对200条问题，存储向量与当前模型结果最小余弦相似度为 {d["config"]["stored_vector_alignment_min"]:.8f}，未发现错配。',
'- 原问题自检200/200命中自身，仅作入库自检，不计入召回率。',
'- 评测296题：200道单意图改写、48道双意图组合、48道负例。每题的原始结果、文本分支和完整向量排名均保存；296/296与源码回放一致。',
'- 这是模板合成开发题，没有保留独立验证/测试集。组合题主要覆盖历史/当前挂科和成绩明细，不能代表所有复杂问法。',
'','## 实际配置与结果','',
'阈值0.4，向量Top K 5，保留双向文本包含匹配。',
'','|指标|结果|','|---|---:|',
'|正例宏平均召回率|83.3%|','|正例宏平均精确率（严格ID）|18.4%|',
'|完全满足且无额外返回|3.4%（10/296）|',
'|单意图召回率|92.5%（185/200）|',
f'|双意图全命中率|16.7%（{extra["multi_full"]}/48）|',
'|负例误召回率|79.2%（38/48）|',
'|域外负例误召回率|58.3%（14/24）|',
'|同域缺少数据的负例误召回率|100%（24/24）|',
'',
'召回和精确率先逐题计算，再对248个正例求平均。所有正例均返回5条，单意图只有1条严格标准时精确率上限就是20%；18.4%不能直接解释为其余示例对SQL生成均无参考价值。需业务复核可替代示例集合。',
f'双意图：两项全中{extra["multi_full"]}题，仅中一项{extra["multi_partial"]}题，两项都未中{extra["multi_none"]}题。',
'',
'本批改写和负例均未触发文本包含分支，最终混合结果与向量结果一致。文本分支在原问题自检会命中，因此不能据此认为源码文本分支损坏。',
'','## 向量排名诊断','',
'以下不应用相似度阈值，只观察单意图正确示例在完整向量排名中的位置，不是调整参数后的正式召回率。','',
'|K|单意图Hit@K|','|---|---:|']
for k,v in hits.items():lines.append(f'|{k}|{v:.1%}|')
lines+=['','## 典型情况','']
for case_id in ['P003','P009','M001','N003','N025']:
 c=next(c for c in d['observations'] if c['id']==case_id)
 lines += [f'### {case_id} {c["question"]}','',
           '标准：'+('；'.join(labels[g[0]] for g in c['required_groups']) or '不返回示例'),
           '实际：'+'；'.join(f'{eid} {labels[eid]}' for eid in c['actual'])]
 if c['vector_gold_rank'] is not None:lines.append('第一个正确示例的向量排名：'+str(c['vector_gold_rank']))
 lines.append('')
lines+=['## 后续测试重点','',
'1. 先复核示例标签和组合题。确定需要“完整回答相同查询”还是“可借鉴的SQL模式”，两种目标的相关性标准不同。',
'2. 在独立的新验证题上评估阈值、Top K和多意图拆分。提高阈值可能减少误召回，但不能解决正确示例排第67位的问题。',
'3. 200条SQL仍含表名占位符；用于SQL生成前需完成实际表映射。本轮未验证SQL执行结果。',
'',
'最终证据使用本目录 report.json 及冻结题集。20260910-172337 为修正3道近原文题前的初跑结果，已被本报告替代。']
(out/'测试报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(extra,ensure_ascii=False))
