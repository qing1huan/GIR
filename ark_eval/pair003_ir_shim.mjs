// Trusted fixed supervisor shim: no candidate JS module or eval route.
import fs from 'node:fs';
import net from 'node:net';
import {spawn} from 'node:child_process';
function send(x){fs.writeSync(1,JSON.stringify(x)+'\n');}
function line(){const b=Buffer.alloc(1),out=[];while(fs.readSync(0,b,0,1,null)){if(b[0]===10)return new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(out));out.push(b[0]);if(out.length>32768)throw Error('BOOT_LIMIT');}throw Error('EOF');}
send({transport:'gir-node-ready@1',node:process.version});
const raw=line(),boot=JSON.parse(raw);
if(boot.purpose==='ir_case'){
  if(Object.keys(boot).sort().join()!=='case,purpose,source,v'||boot.v!=='gir-pair003-ir@1')throw Error('BOOT_KEYS');
  const child=spawn('/gir-python/lib/ld-linux-x86-64.so.2',['--library-path','/gir-python/lib','/gir-python/bin/python3.12','-I','-B','-S','/gir-ir-worker.py'],{env:{PATH:'/usr/bin:/bin',HOME:'/tmp',LANG:'C'},stdio:['pipe','inherit','inherit']});
  child.stdin.end(raw+'\n');
  const code=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',(code,signal)=>resolve(code??(signal==='SIGKILL'?137:1)));});
  process.exitCode=code;
}else if(boot.purpose==='profile_probe'){
  if(Object.keys(boot).sort().join()!=='canary_path,purpose,v'||boot.v!=='gir-node-bootstrap@1')throw Error('BOOT_KEYS');
  const cg=f=>fs.readFileSync('/sys/fs/cgroup/'+f,'utf8').trim();
  const stats=()=>Object.fromEntries(cg('cpu.stat').split('\n').map(x=>x.split(' ')));
  const before=stats(),start=Date.now();while(Date.now()-start<2000){}
  const after=stats();let denial;
  await new Promise(resolve=>{const s=net.connect({host:'1.1.1.1',port:443});s.once('connect',()=>{denial='CONNECTED';s.destroy();resolve();});s.once('error',e=>{denial=e.code;resolve();});s.setTimeout(1000,()=>{denial='TIMEOUT';s.destroy();resolve();});});
  send({transport:'gir-node-probe@1',result:{node:process.version,uid:process.getuid(),memory_max:cg('memory.max'),cpu_max:cg('cpu.max'),pids_max:cg('pids.max'),network_namespace:fs.readlinkSync('/proc/self/ns/net'),network_denial:denial,windows_mounts_absent:!fs.existsSync('/mnt/c')&&!fs.existsSync('/mnt/d'),canary_unreachable:!fs.existsSync(boot.canary_path),no_host_environment:['GIR_NON_SECRET_CANARY','HTTP_PROXY','HTTPS_PROXY','WSLENV'].every(k=>!Object.hasOwn(process.env,k)),cpu_usage_usec:Number(after.usage_usec)-Number(before.usage_usec),throttled_periods:Number(after.nr_throttled)-Number(before.nr_throttled),elapsed_ms:Date.now()-start}});
}else if(boot.purpose==='ir_resource_fixture'){
  if(Object.keys(boot).sort().join()!=='fixture,purpose,v'||boot.v!=='gir-pair003-ir@1')throw Error('BOOT_KEYS');
  if(boot.fixture==='oom'){const keep=[];while(true)keep.push(Buffer.alloc(1024*1024,1));}
  else if(boot.fixture==='tree'){spawn('/bin/sleep',['300'],{stdio:'ignore'});spawn('/bin/sleep',['300'],{stdio:'ignore'});while(true){}}
  else if(boot.fixture==='spin'){while(true){}}
  else throw Error('FIXTURE_REJECTED');
}else throw Error('PURPOSE_REJECTED_NO_JS_EVALUATION');
