"""Fixed authored lifecycle control. No source, candidate, network or credential input."""
import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .pilot24_execution_scope import ROOT,require
from .pilot24_execution_eval import write_json
from .container_profile import clean_environment
from .pilot24_recovery import process_identity,process_alive


def directory(value):
    p=Path(value).absolute()
    require(p.parent==ROOT/'results' and p.name.startswith('pilot24-recovery-lifecycle-control-') and
            p.resolve()==p,'P24R_CONTROL_PATH')
    return p


def units(p):
    tag=hashlib.sha256(str(p).encode()).hexdigest()[:24]
    return {k:'gir-p24-recovery-control-'+tag+'-'+k for k in ('complete','interrupt')}


def call(argv):
    r=subprocess.run(argv,capture_output=True,text=True,timeout=30,cwd=ROOT)
    require(r.returncode==0,'P24R_CONTROL_SERVICE_FAILED')
    return r.stdout


def child(p,mode):
    require(mode in units(p),'P24R_CONTROL_CATALOG_ONLY')
    target=p/mode
    target.mkdir(exist_ok=False)
    write_json(target/'claim.json',{'pid':os.getpid(),'restart':False,'catalog':mode,
                                  'generated_source_executions':0,'model_calls':0})
    time.sleep(1)
    write_json(target/'reservation.json',{'trusted_mock_only':True,'unknown_outcome':'DO_NOT_RETRY'})
    time.sleep(2 if mode=='complete' else 30)
    write_json(target/'result.json',{'status':'TRUSTED_CONTROL_COMPLETED','generated_source_executions':0,
                                    'model_calls':0})
    write_json(target/'summary.json',{'status':'COMPLETE','generated_source_executions':0,'model_calls':0})


def launch(p):
    p.mkdir(exist_ok=False)
    names=units(p)
    write_json(p/'launch.json',{'units':names,'caller_pid':os.getpid(),'model_calls':0})
    for mode,unit in names.items():
        call(['/usr/bin/systemd-run','--user','--unit='+unit,'--property=Type=exec',
              '--property=RemainAfterExit=yes','--property=Restart=no','--property=KillMode=control-group',
              '--property=StandardOutput=null','--property=StandardError=null','--working-directory='+str(ROOT),
              '/usr/bin/python3','-m','ark_eval.pilot24_recovery_control','--directory',str(p),'--child',mode])
    return {'state':'TRUSTED_CONTROLS_DETACHED','units':names,'generated_source_executions':0,'model_calls':0}


def verify(p):
    names=units(p)
    receipt=json.loads((p/'launch.json').read_text(encoding='utf-8'))
    require(receipt['units']==names,'P24R_CONTROL_UNIT_BINDING')
    deadline=time.monotonic()+20
    while not (p/'interrupt/reservation.json').is_file():
        require(time.monotonic()<deadline,'P24R_CONTROL_START_TIMEOUT');time.sleep(.2)
    call(['/usr/bin/systemctl','--user','kill','--signal=KILL',names['interrupt']])
    while not (p/'complete/summary.json').is_file():
        require(time.monotonic()<deadline,'P24R_CONTROL_COMPLETION_TIMEOUT');time.sleep(.2)
    observed={}
    for mode,unit in names.items():
        text=call(['/usr/bin/systemctl','--user','show',unit,
                   '--property=ActiveState,SubState,ExecMainPID,ExecMainStatus,Result,Restart'])
        observed[mode]=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
    require(observed['complete']['Restart']=='no' and observed['interrupt']['Restart']=='no' and
            not (p/'interrupt/result.json').exists() and not (p/'interrupt/summary.json').exists(),
            'P24R_CONTROL_RESTART_OR_UNKNOWN_LOST')
    pid=json.loads((p/'complete/claim.json').read_text(encoding='utf-8'))['pid']
    require(pid!=receipt['caller_pid'] and (p/'complete/result.json').is_file(),
            'P24R_CONTROL_NOT_INDEPENDENT')
    report={'schema':'gir-pilot24-authored-lifecycle-proof@1','status':'PASS',
            'launch_caller_exited_before_verification':True,'independent_controller_pid':pid,
            'completed_after_launch_process_exit':True,'killed_control_reservation_retained':True,
            'killed_control_result_absent':True,'automatic_restart':False,'observed_services':observed,
            'generated_source_executions':0,'model_calls':0,
            'limitation':'Caller-process exit and owned-controller kill are tested; host/WSL shutdown is not simulated.'}
    write_json(p/'proof.json',report)
    for unit in names.values():
        call(['/usr/bin/systemctl','--user','stop',unit])
        subprocess.run(['/usr/bin/systemctl','--user','reset-failed',unit],capture_output=True,timeout=20)
    write_json(p/'cleanup.json',{'owned_control_units_stopped':list(names.values()),'no_claim_removed':True})
    return report


def control_read(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None


def wait_control(path,seconds=10):
    deadline=time.monotonic()+seconds
    while True:
        value=control_read(path)
        if value is not None:return value
        require(time.monotonic()<deadline,'P24Z_CONTROL_RECEIPT_TIMEOUT')
        time.sleep(.1)


def direct_spawn(p,flag,mode):
    return subprocess.Popen(['/usr/bin/python3','-m','ark_eval.pilot24_recovery_control',
        '--directory',str(p),flag,mode],cwd=ROOT,env=clean_environment(),
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        close_fds=True,start_new_session=flag=='--direct-child')


def direct_parent(p,mode):
    require(mode in ('complete','disconnect'),'P24Z_CONTROL_PARENT_CATALOG')
    child=direct_spawn(p,'--direct-child',mode)
    identity=process_identity(child.pid)
    require(identity is not None,'P24Z_CONTROL_CHILD_IDENTITY')
    write_json(p/('parent-'+mode+'.json'),{'parent':process_identity(os.getpid()),'child':identity})
    if mode=='disconnect':time.sleep(30)


def direct_child(p,mode):
    require(mode in ('complete','disconnect','interrupt'),'P24Z_CONTROL_CHILD_CATALOG')
    target=p/mode;target.mkdir(exist_ok=False)
    launch=wait_control(p/'direct-launch.json')
    write_json(target/'claim.json',{'trusted_mock_only':True,'identity':process_identity(os.getpid()),
        'self_cgroup':Path('/proc/self/cgroup').read_text().strip(),
        'candidate_executions':0,'model_calls':0})
    write_json(target/'reservation.json',{'trusted_mock_only':True,'unknown_outcome':'DO_NOT_RETRY'})
    if mode=='interrupt':time.sleep(60)
    else:
        deadline=time.monotonic()+30
        while process_alive(launch['launch_caller']):
            require(time.monotonic()<deadline,'P24Z_CONTROL_CALLER_DID_NOT_EXIT');time.sleep(.1)
        time.sleep(1)
    write_json(target/'result.json',{'status':'TRUSTED_CONTROL_COMPLETED',
        'launch_caller_absent':not process_alive(launch['launch_caller']),
        'candidate_executions':0,'model_calls':0})


def direct_launch(p):
    p.mkdir(exist_ok=False)
    identity=process_identity(os.getpid())
    require(identity is not None,'P24Z_CONTROL_LINUX_REQUIRED')
    write_json(p/'direct-launch.json',{'launch_caller':identity,'route':'direct-wsl',
        'candidate_executions':0,'model_calls':0,'machine_settings_changes':False})
    normal=direct_spawn(p,'--direct-parent','complete');normal_identity=process_identity(normal.pid)
    require(normal.wait(timeout=10)==0,'P24Z_CONTROL_NORMAL_PARENT_FAILED')
    helper=direct_spawn(p,'--direct-parent','disconnect');helper_identity=process_identity(helper.pid)
    receipt=wait_control(p/'parent-disconnect.json')
    wait_control(p/'disconnect/claim.json')
    require(helper_identity is not None and receipt['parent']==helper_identity and
            process_alive(helper_identity),'P24Z_CONTROL_PARENT_IDENTITY_DRIFT')
    helper.send_signal(signal.SIGHUP)
    require(helper.wait(timeout=10)==-signal.SIGHUP,'P24Z_CONTROL_PARENT_SIGNAL_NOT_CONFIRMED')
    interrupted=direct_spawn(p,'--direct-child','interrupt')
    interrupted_identity=process_identity(interrupted.pid)
    require(interrupted_identity is not None,'P24Z_CONTROL_INTERRUPTED_IDENTITY')
    write_json(p/'direct-parent-outcomes.json',{'normal_parent':normal_identity,
        'normal_exit':0,'disconnected_parent':helper_identity,'disconnect_exit':-signal.SIGHUP,
        'interrupted_child':interrupted_identity,'only_owned_parent_signalled':True})
    return {'state':'TRUSTED_DIRECT_CONTROLS_STARTED','candidate_executions':0,'model_calls':0}


def terminate_owned(identity):
    require(callable(getattr(os,'pidfd_open',None)) and callable(getattr(signal,'pidfd_send_signal',None)),
            'P24Z_CONTROL_PIDFD_UNAVAILABLE')
    require(type(identity) is dict and type(identity.get('pid')) is int and identity['pid']>0,
            'P24Z_CONTROL_INTERRUPTED_IDENTITY_DRIFT')
    fd=os.pidfd_open(identity['pid'],0)
    try:
        require(process_identity(identity['pid'])==identity and process_alive(identity),
                'P24Z_CONTROL_INTERRUPTED_IDENTITY_DRIFT')
        signal.pidfd_send_signal(fd,signal.SIGKILL,None,0)
    finally:os.close(fd)


def direct_verify(p):
    launch=wait_control(p/'direct-launch.json')
    outcomes=wait_control(p/'direct-parent-outcomes.json')
    require(not process_alive(launch['launch_caller']) and not process_alive(outcomes['normal_parent']) and
            not process_alive(outcomes['disconnected_parent']),'P24Z_CONTROL_PARENTS_STILL_ALIVE')
    for mode in ('complete','disconnect'):
        result=wait_control(p/mode/'result.json')
        claim=wait_control(p/mode/'claim.json')
        require(result['launch_caller_absent'] is True and claim['self_cgroup']=='0::/non-systemd',
                'P24Z_CONTROL_CONTEXT_OR_CALLER_DRIFT')
    claim=wait_control(p/'interrupt/claim.json');wait_control(p/'interrupt/reservation.json')
    require(claim['identity']==outcomes['interrupted_child'] and process_alive(claim['identity']),
            'P24Z_CONTROL_INTERRUPTED_IDENTITY_DRIFT')
    terminate_owned(claim['identity'])
    deadline=time.monotonic()+10
    while process_alive(claim['identity']):
        require(time.monotonic()<deadline,'P24Z_CONTROL_TERMINATION_UNVERIFIED');time.sleep(.1)
    require(not (p/'interrupt/result.json').exists(),'P24Z_CONTROL_UNKNOWN_RESULT_PRESENT')
    report={'schema':'gir-pilot24-direct-lifecycle-proof@1','status':'PASS',
        'launch_caller_exited_before_completion':True,'ordinary_parent_exit_survived':True,
        'owned_parent_SIGHUP_survived':True,'interrupted_reservation_retained':True,
        'interrupted_result_absent':True,'automatic_restart':False,
        'direct_context_unchanged':True,'systemd_service_launched':False,
        'delegation_or_machine_settings_changes':False,'candidate_executions':0,'model_calls':0,
        'limitation':'Only authored caller exit and owned-parent SIGHUP tested; actual user connection, exec-server process-tree termination, WSL/host shutdown are not tested or guaranteed.'}
    write_json(p/'direct-proof.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',required=True,type=Path)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--launch',action='store_true')
    g.add_argument('--verify',action='store_true');g.add_argument('--child',choices=('complete','interrupt'))
    g.add_argument('--direct-launch',action='store_true');g.add_argument('--direct-verify',action='store_true')
    g.add_argument('--direct-parent',choices=('complete','disconnect'))
    g.add_argument('--direct-child',choices=('complete','disconnect','interrupt'))
    a=p.parse_args();target=directory(a.directory)
    if a.direct_parent:direct_parent(target,a.direct_parent);return
    if a.direct_child:direct_child(target,a.direct_child);return
    if a.direct_launch:print(json.dumps(direct_launch(target)));return
    if a.direct_verify:print(json.dumps(direct_verify(target)));return
    if a.child:child(target,a.child);return
    print(json.dumps(launch(target) if a.launch else verify(target)))


if __name__=='__main__':main()
