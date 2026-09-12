import fs from 'node:fs/promises';
import {FileBlob, SpreadsheetFile} from '@oai/artifact-tool';
const out='D:/SQLBot-main/outputs/sql-examples-200';
await fs.mkdir(out,{recursive:true});
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load('C:/Users/Lenovo/Downloads/SQL 示例库.xlsx'));
const sheet=wb.worksheets.getItemAt(0);
if(process.argv.includes('--inspect')){
 console.log((await wb.inspect({kind:'workbook,sheet,table',maxChars:2000,tableMaxRows:3,tableMaxCols:4})).ndjson);
 const p=await wb.render({sheetName:sheet.name,range:'A1:D2',scale:1,format:'png'});
 await fs.writeFile(out+'/template.png',new Uint8Array(await p.arrayBuffer()));
}else{
 const rows=JSON.parse(await fs.readFile('D:/SQLBot-main/tools/sql_example_recall/corpus.json','utf8')).rows;
 let mapping={datasource:'',tables:{}};
 try{mapping=JSON.parse(await fs.readFile('D:/SQLBot-main/tools/sql_example_recall/import_mapping.json','utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 const values=rows.map(r=>[r.question,r.description.replace(/\{\{(\w+)\}\}/g,(all,key)=>mapping.tables[key]? '"'+mapping.tables[key].replaceAll('"','""')+'"':all),mapping.datasource,'']);
 sheet.getRange('A2:D201').values=values;
 sheet.getRange('A2:D201').format.wrapText=true;
 sheet.getRange('A2:D201').format.verticalAlignment='top';
 sheet.getRange('A:A').format.columnWidth=46;
 sheet.getRange('B:B').format.columnWidth=105;
 sheet.getRange('C:D').format.columnWidth=25;
 sheet.getRange('A1:D1').format.rowHeight=32;
 for(let i=0;i<values.length;i++){
   const lineCount=Math.max(Math.ceil(values[i][0].length/22),Math.ceil(values[i][1].length/90));
   sheet.getRange(`A${i+2}:D${i+2}`).format.rowHeight=Math.max(42,22*(lineCount+1));
 }
 sheet.freezePanes.freezeRows(1);
 wb.recalculate();
 console.log((await wb.inspect({kind:'table',range:`${sheet.name}!A1:D3`,tableMaxRows:3,tableMaxCols:4,maxChars:2000})).ndjson);
 for(const [name,range] of [['head','A1:D5'],['tail','A198:D201']]){
   const p=await wb.render({sheetName:sheet.name,range,scale:1,format:'png'});
   await fs.writeFile(out+'/'+name+'.png',new Uint8Array(await p.arrayBuffer()));
 }
 const complete=Boolean(mapping.datasource)&&values.every(r=>!r[1].includes('{{'));
 const filename=complete?'SQL示例库_200条.xlsx':'SQL示例库_200条_待绑定数据源.xlsx';
 await (await SpreadsheetFile.exportXlsx(wb)).save(out+'/'+filename);
 console.log(JSON.stringify({filename,rows:values.length,readyForImport:complete}));
}
