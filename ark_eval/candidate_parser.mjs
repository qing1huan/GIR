// Trusted parse-only worker. Candidate modules are never evaluated or invoked.
import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';
function line() {
  const bytes=[];const b=Buffer.alloc(1);
  while(fs.readSync(0,b,0,1,null)) {
    if(b[0]===10)return new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(bytes));
    bytes.push(b[0]);if(bytes.length>32768)throw Error('INPUT_LIMIT');
  }
  throw Error('INPUT_CLOSED');
}
function send(value){fs.writeSync(1,JSON.stringify(value)+'\n');}
send({transport:'gir-node-ready@1',node:process.version});
const boot=JSON.parse(line());
if(Object.keys(boot).sort().join()!=='purpose,source,v'||boot.v!=='gir-candidate-parse@1'||
   boot.purpose!=='parse_only'||typeof boot.source!=='string'||Buffer.byteLength(boot.source)>16384)
  throw Error('BOOT_REJECTED');
const result={status:'SYNTAX_REJECTED',exports:[],static_imports:[],
  source_sha256:crypto.createHash('sha256').update(boot.source).digest('hex'),
  top_level_evaluated:false,callback_invoked:false};
try {
  const context=vm.createContext(Object.create(null),{codeGeneration:{strings:false,wasm:false}});
  const module=new vm.SourceTextModule(boot.source,{context});
  result.static_imports=[...module.dependencySpecifiers];
  if(result.static_imports.length)result.status='IMPORTS_REJECTED';
  else {
    await module.link(()=>{throw Error('IMPORT_DENIED');});
    result.exports=Object.getOwnPropertyNames(module.namespace);
    result.status=result.exports.length===1&&result.exports[0]==='onOpen'?'PARSED':'EXPORTS_REJECTED';
  }
} catch(_) {result.status='SYNTAX_REJECTED';}
send({transport:'gir-candidate-parse-result@1',result});
