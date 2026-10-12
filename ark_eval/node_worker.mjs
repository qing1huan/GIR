// Trusted protocol shim. node:vm is not the OS security boundary.
import fs from 'node:fs';
import vm from 'node:vm';
import net from 'node:net';
import {spawn} from 'node:child_process';
const V = 'trusted-ctx@1';
const MAX = 8192;
function line(max = MAX) {
  const bytes = []; const b = Buffer.alloc(1);
  while (fs.readSync(0, b, 0, 1, null)) {
    if (b[0] === 10) return new TextDecoder('utf-8', {fatal:true}).decode(Uint8Array.from(bytes));
    bytes.push(b[0]); if (bytes.length > max) throw Error('INPUT_LIMIT');
  }
  throw Error('INPUT_CLOSED');
}
function send(value) { fs.writeSync(1, JSON.stringify(value) + '\n'); }
send({transport:'gir-node-ready@1', node:process.version});
const boot = JSON.parse(line(32768));
if (boot.v !== 'gir-node-bootstrap@1') throw Error('BOOT_VERSION');
let seq = 0;
function request(kind, extra = {}) {
  const frame = {v:V, invocation:boot.invocation, seq, kind, ...extra};
  send(frame); const response = JSON.parse(line());
  if (response.v !== V || response.invocation !== boot.invocation || response.seq !== seq ||
      response.kind !== (kind === 'done' ? 'done' : 'return')) throw Error('HOST_RESPONSE');
  seq++; return response.value;
}
const api = Object.freeze({
  resolve:entity => request('call', {op:'resolve', args:{entity}}),
  exists:entity => 'some' in request('call', {op:'resolve', args:{entity}}),
  read:(entity,field) => request('call', {op:'read', args:{entity,field}}),
  require:option => request('call', {op:'require', args:{option}}),
  sub:(a,b) => request('call', {op:'int_sub', args:{a,b}}),
  write:(entity,field,value) => request('call', {op:'write', args:{entity,field,value}}),
  add:(entity,field,value) => request('call', {op:'add', args:{entity,field,value}}),
  emit:(event,payload) => request('call', {op:'emit', args:{event,payload}}),
  schedule:(event,payload,delay) => request('call', {op:'schedule', args:{event,payload,delay}}),
  draw:bound => request('call', {op:'draw', args:{bound}}),
});
function deepFreeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); }
  return value;
}
if (boot.purpose === 'callback') {
  if (Object.keys(boot).sort().join() !== 'invocation,payload,purpose,source,v') throw Error('BOOT_KEYS');
  try {
    const context = vm.createContext(Object.create(null), {codeGeneration:{strings:false, wasm:false}});
    const module = new vm.SourceTextModule(boot.source, {context});
    await module.link(() => { throw Error('IMPORT_DENIED'); });
    await module.evaluate();
    if (Object.keys(module.namespace).join() !== 'onOpen' || typeof module.namespace.onOpen !== 'function') throw Error('EXPORT');
    const returned = module.namespace.onOpen(api, deepFreeze(boot.payload));
    if (returned !== undefined) throw Error('RETURN_VALUE');
    request('done');
  } catch (_) { request('fault', {code:'JS_EXCEPTION'}); }
} else if (boot.purpose === 'fault_fixture') {
  if (Object.keys(boot).sort().join() !== 'fixture,invocation,payload,purpose,v') throw Error('BOOT_KEYS');
  // All branches below are fixed, reviewed adversarial protocol fixtures.
  api.write(boot.payload.player, 'keys', 0);
  if (boot.fixture === 'disconnect') while (true) {}
  else if (boot.fixture === 'unknown_op') request('call', {op:'host_shell',args:{command:'fixture-only'}});
  else if (boot.fixture === 'injected_key') request('call', {op:'write',args:{entity:boot.payload.player,field:'keys',value:1,constructor:'fixture-only'}});
  else if (boot.fixture === 'oversized_line') fs.writeSync(1, 'x'.repeat(MAX + 1) + '\n');
  else if (boot.fixture === 'stdout_flood') while (true) fs.writeSync(1, 'x'.repeat(1024));
  else if (boot.fixture === 'stderr_flood') while (true) fs.writeSync(2, 'x'.repeat(1024));
  else if (boot.fixture === 'duplicate_key') fs.writeSync(1, '{"v":"trusted-ctx@1","v":"duplicate"}\n');
  else if (boot.fixture === 'tree_timeout') {
    spawn('/bin/sleep', ['300'], {stdio:'ignore'});
    spawn('/bin/sleep', ['300'], {stdio:'ignore'});
    // Tiny trusted children stay within the same actual 64MiB Node-worker profile.
    while (true) {}
  } else throw Error('UNKNOWN_FIXED_FIXTURE');
} else if (boot.purpose === 'profile_probe') {
  if (Object.keys(boot).sort().join() !== 'canary_path,purpose,v') throw Error('BOOT_KEYS');
  const cgroup = file => fs.readFileSync('/sys/fs/cgroup/' + file, 'utf8').trim();
  const stats = () => Object.fromEntries(cgroup('cpu.stat').split('\n').map(row => row.split(' ')));
  const before = stats(); const start = Date.now(); while (Date.now() - start < 2000) {}
  const after = stats();
  let denial;
  await new Promise(resolve => {
    const socket = net.connect({host:'1.1.1.1', port:443});
    socket.once('connect', () => {denial='CONNECTED'; socket.destroy(); resolve();});
    socket.once('error', error => {denial=error.code; resolve();});
    socket.setTimeout(1000, () => {denial='TIMEOUT'; socket.destroy(); resolve();});
  });
  send({transport:'gir-node-probe@1', result:{node:process.version, uid:process.getuid(),
    memory_max:cgroup('memory.max'), cpu_max:cgroup('cpu.max'), pids_max:cgroup('pids.max'),
    network_namespace:fs.readlinkSync('/proc/self/ns/net'), network_denial:denial,
    windows_mounts_absent:!fs.existsSync('/mnt/c') && !fs.existsSync('/mnt/d'),
    canary_unreachable:!fs.existsSync(boot.canary_path),
    no_host_environment:['GIR_NON_SECRET_CANARY','HTTP_PROXY','HTTPS_PROXY','WSLENV'].every(k => !Object.hasOwn(process.env,k)),
    cpu_usage_usec:Number(after.usage_usec)-Number(before.usage_usec),
    throttled_periods:Number(after.nr_throttled)-Number(before.nr_throttled), elapsed_ms:Date.now()-start}});
} else throw Error('BOOT_PURPOSE');
