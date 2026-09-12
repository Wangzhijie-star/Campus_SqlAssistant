import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
rows=json.loads((HERE/'corpus.json').read_text(encoding='utf-8'))['rows']
def paraphrase(q):
    for a,b in [('查询','帮我检索'),('查找','帮我找出'),('比较','对比')]:q=q.replace(a,b)
    for a,b in [('曾不及格记录','历史挂科记录'),('尚不及格记录','目前仍未通过的记录'),('成绩明细记录','逐条考试成绩'),('加权排名表学生','成绩排名报表里的学生'),('查看','给我展示'),('列出','帮我找出'),('统计','汇总'),('计算','求一下'),('共有多少行','一共有几条数据'),('涉及多少名不同学生','去重后有几位学生'),('平均值','均值'),('最大值','最高值'),('最小值','最低值'),('高于本表平均值','超过这张表的平均水平'),('按学号排序','依照学生编号排列'),('记录数','数据条数'),('去重学生人数','不重复的学生数量'),('名次','排名位置'),('原表','导入的报表'),('前十名','排名不超过第10名')]:
        q=q.replace(a,b)
    return q+'？'
cases=[]
for r in rows:
    q=paraphrase(r['question'])
    assert r['question'] not in q
    cases.append(dict(id='P'+r['example_id'][1:],category='single',question=q,required_groups=[[r['example_id']]],family=r['family_id']))
# Pair distinct intents in the same source domain, avoiding a subset/superset metric.
pairs=[]
for start,end in [(0,35),(35,75),(75,114),(114,180)]:
    ids=list(range(start,end))
    pairs.extend(zip(ids[::2],ids[1::2]))
for i,(a,b) in enumerate(pairs[:48]):
    ra,rb=rows[a],rows[b]
    cases.append(dict(id=f'M{i+1:03}',category='multi',question='请分别回答两项：'+paraphrase(ra['question']).rstrip('？')+'；另外，'+paraphrase(rb['question']),required_groups=[[ra['example_id']],[rb['example_id']]],family='pair-'+ra['family_id']+'-'+rb['family_id']))
negatives=[
'图书馆今天几点关门','学校食堂周末供应早餐吗','如何申请宿舍空调维修','校医院下午有牙科门诊吗','明天去学校是否会下雨','帮我写一段毕业祝福','校园卡丢失后去哪里补办','学校无线网络密码如何修改','怎么预约羽毛球馆','新生报到需要带什么材料','在哪里报名参加志愿活动','校车从东校区几点出发',
'帮我翻译这段英文通知','推荐一部适合周末看的电影','解释一下快速排序的原理','如何煮一碗番茄鸡蛋面','帮我写一封请假邮件','二十四乘以三十五等于多少','今天人民币兑美元汇率是多少','如何给手机更换壁纸','请介绍杭州的旅游景点','帮我设计一周跑步计划','太阳到地球有多远','解释一下什么是黑洞',
'统计每名学生本学期缺勤次数','查询学生图书借阅逾期清单','按宿舍统计本月用电量','查询学生家庭收入和助学金金额','统计每名学生参加社团的活动时长','列出学生心理咨询预约记录','查询每名学生的手机号码','统计校园卡本月餐饮消费总额','查询学生实习企业和实习工资','按课程统计教师课堂评价得分','查询课程授课教师的工资','列出各教室本周空闲时段',
'查询学生宿舍楼栋和床位号','统计各班学生体测肺活量平均值','查询每名学生的毕业论文导师','列出学生的奖学金实际发放日期','统计学生今年的门禁晚归次数','查询每门课程的教材购买价格','计算学生高考成绩平均分','查询学生在校期间的违纪处分明细','统计实验室仪器维修费用','查询每门课程的上课地点和每周课表','统计学生的就业签约率','查询学生请假审批进度']
for i,q in enumerate(negatives):
    cases.append(dict(id=f'N{i+1:03}',category='negative',question=q+'？',required_groups=[],family=f'negative-{i}',negative_type='out_of_domain' if i<24 else 'missing_data'))
assert len(cases)==296 and len({c['question'] for c in cases})==296
data=dict(stage='initial_development_baseline',label_status='合成模板改写，严格指定示例ID标签，待业务复核；未保留独立验证/测试集，不能作为最终泛化结论',
          policy='按请求的查询任务标注，数据恰巧同结果不构成等价；多意图要求分别覆盖两个示例。原问题自检不计入评分。',cases=cases)
(HERE/'questions.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('296 development questions: 200 single, 48 multi, 48 negative')
