"""Hand-authored fixture expectations from TASKS.md, independent of controls.

Only explicit world deltas are expanded here. No algorithm, runtime, starter,
control, or request builder is imported or used to obtain expected outputs.
"""
from copy import deepcopy

A=['world',-1,'genesis',0]; B=['world',-1,'genesis',1]
C=['world',-1,'genesis',2]; X=['world',-1,'genesis',99]
DEFAULTS={
 'M1':({'coins':5,'tokens':3},{'sold':False}),
 'M2':({'equipped':True},{'equipped':False,'locked':False}),
 'C1':({'credits':3},{'completed':True,'claimed':False,'reward':2}),
 'C2':({'hp':5,'shield':2,'immune':False},),
 'R1':({'credits':4},{'reserved':False}),
 'R2':({'remaining':5,'done':False,'paused':False},)}
PAYLOADS={'M1':{'buyer':A,'item':B},'M2':{'oldGear':A,'newGear':B},'C1':{'player':A,'quest':B},
 'C2':{'target':A,'amount':3},'R1':{'wallet':A,'ticket':B,'cancel':False},'R2':{'machine':A,'steps':1}}

def world(*values):return [{'id':deepcopy(id_), 'values':deepcopy(val)} for id_,val in zip((A,B,C),values)]
def event(task,**kw):return {'name':'Open','payload':deepcopy({**PAYLOADS[task],**kw})}
def out(name,**kw):return {'name':name,'payload':deepcopy(kw)}
def tick(inputs,changes=None,events=None,error=None):
    return {'inputs':inputs,'changes':changes or {},'events':events or [],'error':error}

def fixture(task,id_,initial=None,inputs=None,changes=None,events=None,error=None,ticks=None):
    initial=deepcopy(initial if initial is not None else world(*DEFAULTS[task]))
    ticks=ticks or [tick(inputs if inputs is not None else [event(task)],changes,events,error)]
    state=deepcopy(initial);expected=[];steps=[]
    for t in ticks:
        steps.append({'inputs':deepcopy(t['inputs'])})
        if t['error'] is None:
            for ordinal,delta in t['changes'].items():
                target=next(e for e in state if e['id']==["world",-1,"genesis",ordinal])
                target['values'].update(deepcopy(delta))
        expected.append({'entities':deepcopy(state),'status':'ABORTED' if t['error'] else 'COMMITTED',
          'error':t['error'],'events':[] if t['error'] else deepcopy(t['events']),'commands':[]})
    return {'id':id_,'task_id':task,'group':id_[0],'seed':1,'initial':initial,'steps':steps,'expected_ticks':expected}

def cases(task):
    f=lambda id_,**kw:fixture(task,id_,**kw)
    if task=='M1':
        purchased=out('Purchased',buyer=A,item=B);denied=out('PurchaseDenied',buyer=A,item=B)
        return [
         f('N01',changes={0:{'coins':3,'tokens':2},1:{'sold':True}},events=[purchased]),
         f('N02',initial=world({'coins':2,'tokens':1},{'sold':False}),changes={0:{'coins':0,'tokens':0},1:{'sold':True}},events=[purchased]),
         f('N03',initial=world({'coins':5,'tokens':0},{'sold':False}),events=[denied]),
         f('N04',inputs=[event(task),event(task)],changes={0:{'coins':3,'tokens':2},1:{'sold':True}},events=[purchased]),
         f('B01',initial=world({'coins':0},{'sold':False}),error='InvalidEntity'),
         f('B02',initial=world({'coins':5,'tokens':3},{}),error='InvalidEntity'),
         f('B03',initial=world({'coins':5,'tokens':3,'sold':False}),inputs=[event(task,item=A)],changes={0:{'coins':3,'tokens':2,'sold':True}},events=[out('Purchased',buyer=A,item=A)]),
         f('B04',initial=world(*DEFAULTS[task],{}),inputs=[event(task),event(task,item=C)],error='InvalidEntity'),
         f('R01',initial=world({'coins':1,'tokens':3},{'sold':False}),events=[denied]),
         f('R02',initial=world({}, {'sold':True})),
         f('R03',initial=world(*DEFAULTS[task][:1],{}),inputs=[event(task,buyer=X)]),
         f('R04',initial=world({},{'sold':False}),inputs=[event(task,item=X)])]
    if task=='M2':
        equip=out('Equipped',oldGear=A,newGear=B);denied=out('EquipDenied',newGear=B)
        return [
         f('N01',changes={0:{'equipped':False},1:{'equipped':True}},events=[equip]),
         f('N02',initial=world({'equipped':False},DEFAULTS[task][1]),events=[denied]),
         f('N03',initial=world({},DEFAULTS[task][1]),error='InvalidEntity'),
         f('N04',inputs=[event(task),event(task)],changes={0:{'equipped':False},1:{'equipped':True}},events=[equip]),
         f('B01',initial=world({}),inputs=[event(task,oldGear=A,newGear=A)]),
         f('B02',initial=world({}, {'equipped':False,'locked':True}),events=[denied]),
         f('B03',initial=world(*DEFAULTS[task],{}),inputs=[event(task),event(task,oldGear=B,newGear=C)],error='InvalidEntity'),
         f('B04',initial=world({'equipped':True},{'equipped':False}),error='InvalidEntity'),
         f('R01',initial=world({}, {'equipped':True})),
         f('R02',initial=world({'equipped':True},{}),inputs=[event(task,oldGear=X)]),
         f('R03',inputs=[event(task,newGear=X)]),
         f('R04',initial=world({'equipped':True},{'equipped':False,'locked':True}),events=[denied])]
    if task=='C1':
        rewarded=out('Rewarded',player=A,quest=B);blocked=out('RewardBlocked',player=A,quest=B)
        return [
         f('N01',changes={0:{'credits':5},1:{'claimed':True}},events=[rewarded]),
         f('N02',ticks=[tick([event(task)],{0:{'credits':5},1:{'claimed':True}},[rewarded]),tick([event(task)])]),
         f('N03',initial=world({}, {'claimed':True})),
         f('N04',initial=world({'credits':7},{'completed':True,'claimed':False,'reward':3}),changes={0:{'credits':10},1:{'claimed':True}},events=[rewarded]),
         f('B01',initial=world({'credits':8},{'completed':True,'claimed':False,'reward':3}),events=[blocked]),
         f('B02',initial=world({'credits':3},{'completed':False,'reward':2}),error='InvalidEntity'),
         f('B03',initial=world({'credits':3},{'completed':True,'claimed':False}),error='InvalidEntity'),
         f('B04',initial=world(*DEFAULTS[task],{'completed':True,'claimed':False}),inputs=[event(task),event(task,quest=C)],error='InvalidEntity'),
         f('R01',initial=world({}, {'completed':False,'claimed':False})),
         f('R02',initial=world({'credits':3},{}),inputs=[event(task,player=X)]),
         f('R03',inputs=[event(task,quest=X)]),
         f('R04',initial=world({'credits':10},{'completed':True,'claimed':False,'reward':1}),events=[blocked])]
    if task=='C2':
        hit=out('Hit',target=A);died=out('Died',target=A)
        return [
         f('N01',initial=world({'hp':5,'immune':True})),
         f('N02',initial=world({'hp':5,'shield':5,'immune':False}),changes={0:{'shield':2}},events=[hit]),
         f('N03',initial=world({'hp':5,'shield':1,'immune':False}),changes={0:{'hp':3,'shield':0}},events=[hit]),
         f('N04',initial=world({'hp':2,'shield':1,'immune':False}),changes={0:{'hp':0,'shield':0}},events=[hit,died]),
         f('B01',initial=world({'hp':5,'shield':3,'immune':False}),changes={0:{'shield':0}},events=[hit]),
         f('B02',initial=world({'hp':5,'shield':2}),error='InvalidEntity'),
         f('B03',initial=world({'hp':5,'immune':False}),error='InvalidEntity'),
         f('B04',initial=world(DEFAULTS[task][0],{'hp':5,'immune':False}),inputs=[event(task),event(task,target=B)],error='InvalidEntity'),
         f('R01',initial=world({'hp':5,'shield':0,'immune':False}),inputs=[event(task,amount=2)],changes={0:{'hp':3}},events=[hit]),
         f('R02',initial=world({'hp':0})),
         f('R03',initial=world({}),inputs=[event(task,amount=0)]),
         f('R04',inputs=[event(task,target=X)])]
    if task=='R1':
        released=out('Released',wallet=A,ticket=B);reserved=out('Reserved',wallet=A,ticket=B)
        return [
         f('N01',initial=world({'credits':4},{'reserved':True}),inputs=[event(task,cancel=True)],changes={0:{'credits':6},1:{'reserved':False}},events=[released]),
         f('N02',initial=world({'credits':4},{'reserved':True}),inputs=[event(task,cancel=True),event(task,cancel=True)],changes={0:{'credits':6},1:{'reserved':False}},events=[released]),
         f('N03',initial=world({'credits':8},{'reserved':True}),inputs=[event(task,cancel=True)],changes={0:{'credits':10},1:{'reserved':False}},events=[released]),
         f('N04',inputs=[event(task),event(task,cancel=True)],events=[reserved,released]),
         f('B01',initial=world({'credits':9},{'reserved':True}),inputs=[event(task,cancel=True)],events=[out('ReleaseDenied',wallet=A,ticket=B)]),
         f('B02',initial=world({'credits':4},{}),inputs=[event(task,cancel=True)],error='InvalidEntity'),
         f('B03',initial=world({}, {'reserved':False}),inputs=[event(task,cancel=True)]),
         f('B04',initial=world({'credits':4},{'reserved':True},{}),inputs=[event(task,cancel=True),event(task,ticket=C,cancel=True)],error='InvalidEntity'),
         f('R01',changes={0:{'credits':2},1:{'reserved':True}},events=[reserved]),
         f('R02',initial=world({'credits':1},{'reserved':False}),events=[out('Insufficient',wallet=A,ticket=B)]),
         f('R03',initial=world({}, {'reserved':True})),
         f('R04',initial=world({}, {'reserved':False}),inputs=[event(task,ticket=X)])]
    if task=='R2':
        built=out('Built',machine=A)
        return [
         f('N01',inputs=[event(task,steps=3)],changes={0:{'remaining':2}}),
         f('N02',initial=world({'remaining':2,'done':False,'paused':False}),inputs=[event(task,steps=3)],changes={0:{'remaining':0,'done':True}},events=[built]),
         f('N03',initial=world({'done':False,'paused':True}),inputs=[event(task,steps=2)]),
         f('N04',initial=world({'done':False}),inputs=[event(task,steps=0)]),
         f('B01',initial=world({'remaining':0,'done':False,'paused':False}),inputs=[event(task,steps=2)],changes={0:{'done':True}},events=[built]),
         f('B02',initial=world({'remaining':5,'done':False}),error='InvalidEntity'),
         f('B03',initial=world({'done':False,'paused':False}),error='InvalidEntity'),
         f('B04',initial=world(DEFAULTS[task][0],{'remaining':5,'done':False}),inputs=[event(task),event(task,machine=B)],error='InvalidEntity'),
         f('R01',changes={0:{'remaining':4}}),
         f('R02',initial=world({'remaining':1,'done':False,'paused':False}),changes={0:{'remaining':0,'done':True}},events=[built]),
         f('R03',initial=world({'done':True}),inputs=[event(task,steps=3)]),
         f('R04',inputs=[event(task,machine=X,steps=3)])]
    raise ValueError('Unknown oracle task')

def v0_cases(task):
    f=lambda id_,**kw:fixture(task,id_,**kw)
    preserved=[deepcopy(c) for c in cases(task) if c['group']=='R']
    if task=='M1':
        ev=out('Purchased',buyer=A,item=B)
        extra=[f('S01',changes={0:{'coins':3},1:{'sold':True}},events=[ev]),
         f('S02',inputs=[event(task),event(task)],changes={0:{'coins':3},1:{'sold':True}},events=[ev]),
         f('S03',initial=world({}, {'sold':False}),error='InvalidEntity'),
         f('S04',initial=world({'coins':2},{'sold':False}),changes={0:{'coins':0},1:{'sold':True}},events=[ev])]
    elif task=='M2':
        ev=out('Equipped',oldGear=A,newGear=B)
        extra=[f('S01',changes={1:{'equipped':True}},events=[ev]),
         f('S02',inputs=[event(task),event(task)],changes={1:{'equipped':True}},events=[ev]),
         f('S03',initial=world({'equipped':True},{}),error='InvalidEntity'),
         f('S04',initial=world({}),inputs=[event(task,newGear=A)])]
    elif task=='C1':
        ev=out('Rewarded',player=A,quest=B)
        extra=[f('S01',changes={0:{'credits':4}},events=[ev]),
         f('S02',inputs=[event(task),event(task)],changes={0:{'credits':5}},events=[ev,ev]),
         f('S03',initial=world({}, {'completed':True}),error='InvalidEntity'),
         f('S04',initial=world({'credits':9},{'completed':True}),changes={0:{'credits':10}},events=[ev])]
    elif task=='C2':
        ev=out('Hit',target=A)
        extra=[f('S01',changes={0:{'hp':2}},events=[ev]),
         f('S02',initial=world({'hp':2}),changes={0:{'hp':0}},events=[ev,out('Died',target=A)]),
         f('S03',initial=world({}),error='InvalidEntity'),
         f('S04',initial=world({'hp':2}),inputs=[event(task),event(task)],changes={0:{'hp':0}},events=[ev,out('Died',target=A)])]
    elif task=='R1':
        extra=[f('S01',inputs=[event(task,cancel=True)],events=[out('CancelUnavailable',ticket=B)]),
         f('S02',initial=world({},{}),inputs=[event(task,cancel=True)],events=[out('CancelUnavailable',ticket=B)]),
         f('S03',initial=world({'credits':4},{}),error='InvalidEntity'),
         f('S04',inputs=[event(task),event(task)],changes={0:{'credits':2},1:{'reserved':True}},events=[out('Reserved',wallet=A,ticket=B)])]
    else:
        extra=[f('S01',inputs=[event(task,steps=3)],changes={0:{'remaining':4}}),
         f('S02',initial=world({'remaining':2,'done':False}),inputs=[event(task),event(task)],changes={0:{'remaining':0,'done':True}},events=[out('Built',machine=A)]),
         f('S03',initial=world({'done':False}),error='InvalidEntity'),
         f('S04',inputs=[event(task,steps=0)],changes={0:{'remaining':4}})]
    return preserved+extra
