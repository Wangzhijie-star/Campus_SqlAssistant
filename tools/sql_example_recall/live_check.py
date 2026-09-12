import os,sys,json,hashlib,math
from pathlib import Path
root=Path('/home/sqlbotdev/sqlbot-local/backend')
os.chdir(root);sys.path.insert(0,str(root))
from sqlalchemy import text
from common.core.db import engine
from common.core.config import settings
with engine.connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    rows=[dict(r) for r in c.execute(text('SELECT id,oid,datasource,advanced_application,question,description,enabled,embedding::text AS vector FROM data_training ORDER BY id')).mappings()]
    ds=[dict(r) for r in c.execute(text('SELECT id,name,type,oid FROM core_datasource ORDER BY id')).mappings()]
    tables=[dict(r) for r in c.execute(text('SELECT ds_id,table_name,table_comment FROM core_table ORDER BY ds_id,table_name')).mappings()]
for r in rows:
    v=json.loads(r.pop('vector')) if r['vector'] else None
    r['dimensions']=len(v) if v else None
    r['vector_valid']=bool(v) and all(math.isfinite(x) for x in v) and sum(x*x for x in v)>0
    r['vector_sha256']=hashlib.sha256(json.dumps(v).encode()).hexdigest()
out=dict(rows=rows,datasources=ds,tables=tables,threshold=settings.EMBEDDING_DATA_TRAINING_SIMILARITY,top_k=settings.EMBEDDING_DATA_TRAINING_TOP_COUNT,embedding_enabled=settings.EMBEDDING_ENABLED)
Path(__file__).with_name('live_snapshot.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(count=len(rows),enabled=sum(bool(r['enabled']) for r in rows),invalid_vectors=sum(not r['vector_valid'] for r in rows),dimensions=sorted({str(r['dimensions']) for r in rows}),placeholder_sql=sum('{{' in (r['description'] or '') for r in rows),datasources=ds,threshold=out['threshold'],top_k=out['top_k']),ensure_ascii=False))
