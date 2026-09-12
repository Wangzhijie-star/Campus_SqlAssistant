"""Build 200 SQL templates from inspected campus XLS headers; no DB writes."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROWS = []


def col(name):
    return '"' + name.replace('"', '""') + '"'


def add(table, question, sql, note, family):
    ROWS.append(dict(example_id=f'E{len(ROWS)+1:03}', family_id=family,
                     question=question, description=sql + ';',
                     tables=[table] if isinstance(table, str) else table,
                     business_rule=note, enabled=True, review_status='待业务复核'))


def main():
    configs = [
        ('history', '曾不及格记录', ['学年学期','班级','专业方向','专业','学院','课程号','课程名'], ['学分'], '课程成绩'),
        ('current', '尚不及格记录', ['院系','专业名','专业方向','班级号','班名','入学年级','课程名'], ['学分','不及格次数'], '成绩'),
        ('detail', '成绩明细记录', ['课程号','课序号','课程名','考试时间','修读方式','课程属性','班级'], ['学分'], '总成绩'),
        ('ranking', '加权排名表学生', ['班级','专业方向','专业','院系'], ['GPA','加权学分成绩','平均成绩','尚不及格学分'], None),
    ]
    for table, label, dimensions, metrics, score in configs:
        t = '{{' + table + '}}'
        note = '仅统计该导入快照；学号按文本保存。曾不及格不等于当前挂科。排名表指标使用原表值。'
        add(table, f'查看全部{label}，按学号排序', f'SELECT * FROM {t} ORDER BY "学号"', note, table+'-detail')
        add(table, f'{label}共有多少行', f'SELECT COUNT(*) AS "记录数" FROM {t}', note+'记录数不等于课程数或人数。', table+'-rows')
        add(table, f'{label}涉及多少名不同学生', f'SELECT COUNT(DISTINCT "学号") AS "人数" FROM {t}', note, table+'-students')
        add(table, f'列出{label}中出现的学生学号', f'SELECT DISTINCT "学号" FROM {t} WHERE "学号" IS NOT NULL ORDER BY "学号"', note, table+'-student-list')
        add(table, f'统计{label}中每名学生的记录行数', f'SELECT "学号", COUNT(*) AS "记录数" FROM {t} GROUP BY "学号"', note, table+'-per-student')
        add(table, f'找出{label}中有多行记录的学生学号', f'SELECT "学号", COUNT(*) AS "记录数" FROM {t} GROUP BY "学号" HAVING COUNT(*) > 1', note+'此查询不把多行直接判定为重复数据。', table+'-multiple')
        for dim in dimensions:
            d = col(dim)
            add(table, f'按{dim}统计{label}的记录数', f'SELECT {d}, COUNT(*) AS "记录数" FROM {t} GROUP BY {d}', note, table+'-group-rows-'+dim)
            add(table, f'按{dim}统计{label}涉及的去重学生人数', f'SELECT {d}, COUNT(DISTINCT "学号") AS "人数" FROM {t} GROUP BY {d}', note, table+'-group-students-'+dim)
        for metric in metrics:
            m=col(metric)
            for operation, labelop, func in [('avg','平均值','AVG'),('max','最大值','MAX'),('min','最小值','MIN')]:
                add(table, f'计算{label}的{metric}{labelop}', f'SELECT {func}({m}) AS "结果" FROM {t}', note+'忽略空值，不把空值补为0。', table+'-'+metric+'-'+operation)
            add(table, f'列出{label}中{metric}高于本表平均值的记录', f'SELECT * FROM {t} WHERE {m} > (SELECT AVG({m}) FROM {t})', note, table+'-'+metric+'-above-avg')
        if score:
            s=col(score)
            # Numeric score cast is conditional: descriptive grades stay excluded.
            num=f"CASE WHEN TRIM(CAST({s} AS TEXT)) ~ '^[0-9]+([.][0-9]+)?$' THEN CAST(TRIM(CAST({s} AS TEXT)) AS NUMERIC) END"
            for question, condition in [('分数为空', f'{s} IS NULL'), ('分数为零', f'({num}) = 0'), ('数值分数低于60', f'({num}) < 60'), ('数值分数达到60', f'({num}) >= 60'), ('数值分数达到90', f'({num}) >= 90')]:
                add(table, f'列出{label}中{question}的行', f'SELECT * FROM {t} WHERE {condition}', note+'只对数值分数做区间判断，等级制不擅自换算；零分纳入低于60的条件。', table+'-score-'+question)
        if table in ['history','current','detail']:
            add(table, f'{label}包含多少门不同课程', f'SELECT COUNT(DISTINCT "课程号") AS "课程数" FROM {t}', note, table+'-course-count')
            add(table, f'按学号统计{label}中的不同课程数量', f'SELECT "学号", COUNT(DISTINCT "课程号") AS "课程数" FROM {t} GROUP BY "学号"', note, table+'-student-courses')
            add(table, f'找出{label}中同一学生同一课程出现多次的组合', f'SELECT "学号", "课程号", COUNT(*) AS "记录数" FROM {t} GROUP BY "学号", "课程号" HAVING COUNT(*) > 1', note+'多次出现不自动等于错误重复或考试次数。', table+'-repeat-course')

    # Source-provided ranking scope must be preserved.
    for metric in ['GPA','加权','平均成绩']:
        for scope in ['专业','专业方向','班级']:
            field=metric+scope+'排名'
            f=col(field)
            add('ranking', f'列出原表{field}前十名学生及名次', f'SELECT "学号", "姓名", {f} FROM {{{{ranking}}}} WHERE {f} BETWEEN 1 AND 10 ORDER BY {f}, "学号"', '按原表名次<=10筛选，保留并列；不在单班样本内重算专业名次。', 'ranking-top-'+field)
            add('ranking', f'查看原表每名学生的{field}', f'SELECT "学号", "姓名", {f} FROM {{{{ranking}}}} ORDER BY {f} NULLS LAST, "学号"', '读取已有排名，不从当前样本重新计算。', 'ranking-list-'+field)

    for field in ['要求总学分','已修课程学分','已修自主实践学分','曾不及格学分','尚不及格学分']:
        f=col(field)
        add('ranking', f'查看各学生原表中的{field}', f'SELECT "学号", "姓名", {f} FROM {{{{ranking}}}} ORDER BY "学号"', '直接读取报表指标，不能用明细记录学分求和替代。', 'ranking-credit-'+field)
        add('ranking', f'按班级计算原表{field}的平均值', f'SELECT "班级", AVG({f}) AS "平均值" FROM {{{{ranking}}}} GROUP BY "班级"', '只对非空数值求平均。', 'ranking-credit-avg-'+field)

    # Twenty cross-table intentions, using EXISTS to avoid join multiplication.
    for left, right, ln, rn in [('history','current','曾不及格','尚不及格'),('current','history','尚不及格','曾不及格'),('ranking','current','排名表','尚不及格'),('ranking','history','排名表','曾不及格'),('detail','current','成绩明细','尚不及格')]:
        for exists, wording in [(True,'存在'),(False,'不存在')]:
            predicate=f'{"EXISTS" if exists else "NOT EXISTS"} (SELECT 1 FROM {{{{{right}}}}} b WHERE b."学号" = a."学号")'
            for count in [False,True]:
                projection='COUNT(DISTINCT a."学号") AS "人数"' if count else 'DISTINCT a."学号"'
                add([left,right], f'{"统计人数：" if count else "列出学号："}{ln}表中，在{rn}表{wording}记录的学生', f'SELECT {projection} FROM {{{{{left}}}}} a WHERE a."学号" IS NOT NULL AND {predicate}', '按学号判断是否存在，避免一对多关联放大；不在尚不及格表仅表示快照中无记录，不能直接证明已经补考通过。', f'cross-{left}-{right}-{exists}-{count}')

    # Additional distinct business calculations grounded in the inspected headers.
    extras = [
        ('current','各学生当前未通过记录中的不及格次数最大值','SELECT "学号", MAX("不及格次数") AS "最多次数" FROM {{current}} GROUP BY "学号"'),
        ('current','查询不及格次数至少两次的当前未通过课程记录','SELECT * FROM {{current}} WHERE "不及格次数" >= 2'),
        ('current','按课程统计当前未通过记录的不及格次数平均值','SELECT "课程号", AVG("不及格次数") AS "平均次数" FROM {{current}} GROUP BY "课程号"'),
        ('history','按学年学期及课程统计曾不及格人数','SELECT "学年学期", "课程号", COUNT(DISTINCT "学号") AS "人数" FROM {{history}} GROUP BY "学年学期", "课程号"'),
        ('detail','按课程属性和修读方式统计成绩记录数','SELECT "课程属性", "修读方式", COUNT(*) AS "记录数" FROM {{detail}} GROUP BY "课程属性", "修读方式"'),
        ('detail','按课程和课序号统计成绩记录人数','SELECT "课程号", "课序号", COUNT(DISTINCT "学号") AS "人数" FROM {{detail}} GROUP BY "课程号", "课序号"'),
        ('ranking','列出原表中尚不及格学分大于零的学生','SELECT "学号", "姓名", "尚不及格学分" FROM {{ranking}} WHERE "尚不及格学分" > 0'),
        ('ranking','列出曾不及格学分大于零但尚不及格学分为零的学生','SELECT "学号", "姓名" FROM {{ranking}} WHERE "曾不及格学分" > 0 AND "尚不及格学分" = 0'),
        ('ranking','计算各学生要求总学分减去已修课程学分的差值','SELECT "学号", "要求总学分" - "已修课程学分" AS "差值" FROM {{ranking}}'),
        ('ranking','同时查看学生GPA、加权学分成绩和平均成绩','SELECT "学号", "姓名", "GPA", "加权学分成绩", "平均成绩" FROM {{ranking}}'),
        ('ranking','按班级统计有尚不及格学分的学生人数','SELECT "班级", COUNT(DISTINCT "学号") AS "人数" FROM {{ranking}} WHERE "尚不及格学分" > 0 GROUP BY "班级"'),
        ('ranking','列出GPA班级排名与加权班级排名不一致的学生','SELECT "学号", "GPA班级排名", "加权班级排名" FROM {{ranking}} WHERE "GPA班级排名" <> "加权班级排名"'),
        ('detail','仅查看重修方式的成绩明细','SELECT * FROM {{detail}} WHERE "修读方式" = \'重修\''),
        ('detail','仅查看正常修读的成绩明细','SELECT * FROM {{detail}} WHERE "修读方式" = \'正常\''),
        ('detail','统计各课程的重修学生人数','SELECT "课程号", COUNT(DISTINCT "学号") AS "人数" FROM {{detail}} WHERE "修读方式" = \'重修\' GROUP BY "课程号"'),
        ('detail','按课程属性统计不同课程数','SELECT "课程属性", COUNT(DISTINCT "课程号") AS "课程数" FROM {{detail}} GROUP BY "课程属性"'),
        ('history','统计各学生涉及多少个历史不及格学年学期','SELECT "学号", COUNT(DISTINCT "学年学期") AS "学期数" FROM {{history}} GROUP BY "学号"'),
        ('history','查找曾在至少两个学年学期出现不及格记录的学生','SELECT "学号" FROM {{history}} GROUP BY "学号" HAVING COUNT(DISTINCT "学年学期") >= 2'),
        ('current','按入学年级和课程统计尚不及格人数','SELECT "入学年级", "课程号", COUNT(DISTINCT "学号") AS "人数" FROM {{current}} GROUP BY "入学年级", "课程号"'),
        ('ranking','查询原表GPA缺失的学生','SELECT "学号", "姓名" FROM {{ranking}} WHERE "GPA" IS NULL'),
        ('detail','统计各考试时间涉及的不同课程数','SELECT "考试时间", COUNT(DISTINCT "课程号") AS "课程数" FROM {{detail}} GROUP BY "考试时间"'),
        ('ranking','比较各学生的加权学分成绩与平均成绩差值','SELECT "学号", "加权学分成绩" - "平均成绩" AS "差值" FROM {{ranking}}'),
    ]
    for i,(table,q,sql) in enumerate(extras):
        add(table,q,sql,'按原表字段计算；学分差值不代表官方毕业缺口，缺失值不补零。',f'custom-{i}')
    assert len(ROWS)==200, len(ROWS)
    assert len({r['question'] for r in ROWS})==200
    numeric_fields=['学分','不及格次数','GPA','加权学分成绩','平均成绩','要求总学分','已修课程学分','已修自主实践学分','曾不及格学分','尚不及格学分']
    for row in ROWS:
        for field in numeric_fields:
            token=col(field)
            row['description']=row['description'].replace(token, f"CAST(NULLIF(TRIM(CAST({token} AS TEXT)), '') AS NUMERIC)")
        for metric in ['GPA','加权','平均成绩']:
            for scope in ['专业','专业方向','班级']:
                token=col(metric+scope+'排名')
                row['description']=row['description'].replace(token, f"CAST(NULLIF(SPLIT_PART(TRIM(CAST({token} AS TEXT)), '/', 1), '') AS NUMERIC)")
        row['business_rule'] += ' 数值文本先去空格再转换；排名形如15/1862时只取斜杠前名次，原字段仍保留。'
    corpus=dict(status='SQL模板已生成；待导入表映射、类型校验和业务复核', dialect='PostgreSQL',
                source_header_row=2, table_placeholders=['history','current','detail','ranking'], rows=ROWS)
    path=HERE/'corpus.json'
    path.write_text(json.dumps(corpus,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    doc=['# 200 条校园 SQL 示例（待业务复核）','',
         '依据四份 XLS 的第二行字段设计。`{{history}}`、`{{current}}`、`{{detail}}`、`{{ranking}}` 必须替换为导入后的真实表名。数值字段须正确导入为数值类型。尚未执行数据库校验或真实召回。','']
    for r in ROWS:
        doc += [f"## {r['example_id']} {r['question']}",'', '```sql',r['description'],'```','',r['business_rule'],'']
    (HERE/'200条SQL示例.md').write_text('\n'.join(doc),encoding='utf-8')
    print(json.dumps({'count':len(ROWS),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))


if __name__=='__main__':
    main()
