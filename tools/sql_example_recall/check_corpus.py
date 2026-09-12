"""Read XLS, freeze structural evidence, smoke execute SQL via SQLite adapter.

This is NOT PostgreSQL execution or a retrieval benchmark. No student rows saved.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'tools/campus_recall/.deps'))
import xlrd


def main():
    db=sqlite3.connect(':memory:')
    db.create_function('SPLIT_PART',3,lambda x,sep,n: x.split(sep)[n-1] if x is not None else None)
    db.create_function('REGEXP',2,lambda pattern,value: bool(re.search(pattern,str(value))) if value is not None else None)
    files={'history':'2404曾不及格.xls.xls','current':'2404尚不及格.xls','detail':'2404学生成绩明细.xls','ranking':'2404加权排名.xls.xls'}
    snapshot=[]
    for table,name in files.items():
        p=ROOT/'项目学习文档/学情分析数据样本'/name
        s=xlrd.open_workbook(str(p)).sheet_by_index(0)
        headers=s.row_values(1)
        if not all(headers) or len(set(headers))!=len(headers):
            raise ValueError('Invalid source headers')
        defs=','.join('"'+h+'"' for h in headers)
        db.execute(f'CREATE TABLE {table} ({defs})')
        values=[[None if str(v).strip()=='' else v for v in s.row_values(r)] for r in range(2,s.nrows)]
        db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" for _ in headers)})',values)
        snapshot.append(dict(logical_table=table,file=name,sheet=s.name,header_row=2,data_rows=len(values),
                             headers=headers,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    corpus=json.loads((HERE/'corpus.json').read_text(encoding='utf-8'))
    results=[]
    for row in corpus['rows']:
        sql=row['description']
        for table in files: sql=sql.replace('{{'+table+'}}',table)
        sql=sql.replace(' ~ ', ' REGEXP ')
        if '{{' in sql: raise ValueError('Unknown placeholder')
        try:
            result=db.execute(sql).fetchall()
            results.append(dict(example_id=row['example_id'],ok=True,result_rows=len(result)))
        except Exception as e:
            results.append(dict(example_id=row['example_id'],ok=False,error=str(e)))
    report=dict(engine='SQLite with PostgreSQL SPLIT_PART/regex adapters',
                limitation='Smoke check only: SQLite numeric casts are more permissive. Not PostgreSQL validation, semantic gold validation, or retrieval measurement.',
                corpus_sha256=hashlib.sha256((HERE/'corpus.json').read_bytes()).hexdigest(),
                passed=sum(r['ok'] for r in results),total=len(results),results=results)
    (HERE/'source_snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (HERE/'sql_smoke_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}))
    if report['passed']!=200: raise SystemExit(1)


if __name__=='__main__': main()
