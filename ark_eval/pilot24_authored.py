"""Repository-authored offline controls. No runtime-derived expected results."""
import copy
import json
from pathlib import Path

TASKS = ('M1', 'M2', 'C1', 'C2', 'R1', 'R2')
ROOT = Path(__file__).with_name('pilot24')

def v(name): return {'op':'var','name':name}
def lit(value): return {'op':'literal','type':'bool' if type(value) is bool else 'int','value':value}
def op(opname, **kw): return {'op':opname, **kw}
def b(name, a, c): return op(name,a=a,b=c)
def neg(a): return op('not',value=a)
def sub(a,c): return b('sub',a,c)
def plus(a,c): return sub(a,sub(lit(0),c))
def req(entity,field): return op('require',value=op('read',entity=v(entity),field=field))
def let(name,value): return op('let',name=name,value=value)
def iff(condition,yes,no=None): return op('if',condition=condition,then=yes,**{'else':no or []})
def wr(entity,field,value): return op('write',entity=v(entity),field=field,value=value)
def emit(event,*names): return op('emit',event=event,payload=op('record',items={n:v(n) for n in names}))
def exists(*names):
    values=[op('is_some',value=op('resolve',entity=v(n))) for n in names]
    return values[0] if len(values)==1 else b('and',values[0],values[1])

META={
 'M1':({'buyer':'EntityID','item':'EntityID'}, {'coins':(0,10),'tokens':(0,10),'sold':None}, {'Purchased':('buyer','item'),'PurchaseDenied':('buyer','item')}),
 'M2':({'oldGear':'EntityID','newGear':'EntityID'}, {'equipped':None,'locked':None}, {'Equipped':('oldGear','newGear'),'EquipDenied':('newGear',)}),
 'C1':({'player':'EntityID','quest':'EntityID'}, {'credits':(0,10),'reward':(1,3),'completed':None,'claimed':None}, {'Rewarded':('player','quest'),'RewardBlocked':('player','quest')}),
 'C2':({'target':'EntityID','amount':'int'}, {'hp':(0,10),'shield':(0,10),'immune':None}, {'Hit':('target',),'Died':('target',)}),
 'R1':({'wallet':'EntityID','ticket':'EntityID','cancel':'bool'}, {'credits':(0,10),'reserved':None}, {'Reserved':('wallet','ticket'),'Insufficient':('wallet','ticket'),'Released':('wallet','ticket'),'ReleaseDenied':('wallet','ticket'),'CancelUnavailable':('ticket',)}),
 'R2':({'machine':'EntityID','steps':'int'}, {'remaining':(0,10),'done':None,'paused':None}, {'Built':('machine',)})}

def abi(task):
    payload,fields,outputs=META[task]
    return {'version':'core-v0.1','fields':{n:{'type':'int' if bounds else 'bool','authority':'IR','policy':'exclusive',**({'range':list(bounds)} if bounds else {})} for n,bounds in fields.items()},
      'events':{'Tick':{'record':{}},'Open':{'record':payload},**{n:{'record':{k:'EntityID' for k in members}} for n,members in outputs.items()}},
      'handlers':[{'id':'open','event':'Open','priority':0,'body':[]}],'services':{},'extensions':{}}

def ir_body(task,version,mutant=0):
    new=version==1
    if task=='M1':
        denied=[emit('PurchaseDenied','buyer','item')]
        success=[wr('buyer','coins',sub(v('coins'),v('cost')))]
        if new and mutant!=1:success.append(wr('buyer','tokens',sub(v('tokens'),lit(1))))
        success += [wr('item','sold',lit(True)),emit('Purchased','buyer','item')]
        condition=b('ge',v('coins'),v('cost'))
        if new:condition=b('and',condition,b('ge',v('tokens'),lit(1)))
        decision=[iff(condition,success,denied)]
        if new and mutant==2:
            decision=[iff(b('ge',v('coins'),v('cost')),[let('tokens',req('buyer','tokens'))]+decision,denied)]
        elif new:decision=[let('tokens',req('buyer','tokens'))]+decision
        branch=[let('cost',lit(2)),let('coins',req('buyer','coins'))]+decision
        prefix=[let('early',req('buyer','coins'))] if mutant==3 else []
        return [iff(exists('buyer','item'),prefix+[iff(neg(req('item','sold')),branch)])]
    if task=='M2':
        success=([wr('oldGear','equipped',lit(False))] if new and mutant!=1 else [])+[wr('newGear','equipped',lit(True)),emit('Equipped','oldGear','newGear')]
        if new:success=[let('oldEquipped',req('oldGear','equipped')),iff(v('oldEquipped'),success,[emit('EquipDenied','newGear')])]
        active=[iff(neg(req('newGear','equipped')),[iff(req('newGear','locked'),[emit('EquipDenied','newGear')],success)])]
        if mutant==3:active=[let('early',req('oldGear','equipped'))]+active
        if mutant!=2:active=[iff(neg(b('eq',v('oldGear'),v('newGear'))),active)]
        return [iff(exists('oldGear','newGear'),active)]
    if task=='C1':
        total=plus(v('credits'),v('reward')) if new and mutant!=1 else plus(v('credits'),lit(1))
        success=[wr('player','credits',v('nextCredits'))]+([wr('quest','claimed',lit(True))] if new else [])+[emit('Rewarded','player','quest')]
        denied=([wr('quest','claimed',lit(True))] if mutant==2 else [])+[emit('RewardBlocked','player','quest')]
        decision=[let('credits',req('player','credits'))]
        if new:decision=[let('reward',req('quest','reward'))]+decision
        decision += [let('nextCredits',total),iff(b('le',v('nextCredits'),lit(10)),success,denied)]
        incomplete=[let('early',req('player','credits'))] if mutant==3 else []
        active=incomplete+[iff(req('quest','completed'),decision)]
        if new:active=[iff(neg(req('quest','claimed')),active)]
        return [iff(exists('player','quest'),active)]
    if task=='C2':
        hit=[emit('Hit','target'),iff(b('eq',v('nextHp'),lit(0)),[emit('Died','target')])]
        direct=[let('nextHp',op('select',condition=b('ge',v('hp'),v('amount')),then=sub(v('hp'),v('amount')),**{'else':lit(0)})),wr('target','hp',v('nextHp'))]+hit
        if new:
            absorbed=op('select',condition=b('ge',v('shield'),v('amount')),then=v('amount'),**{'else':v('shield')})
            remaining=sub(v('amount'),v('absorbed'))
            hp_value=op('select',condition=b('ge',v('hp'),v('damage')),then=sub(v('hp'),v('damage')),**{'else':lit(0)})
            settle=[let('shield',req('target','shield')),let('absorbed',absorbed),let('damage',remaining),let('nextShield',sub(v('shield'),v('absorbed'))),let('nextHp',hp_value),wr('target','shield',v('nextShield')),wr('target','hp',v('nextHp'))]+hit
            if mutant==1:
                settle=copy.deepcopy(settle)
                next(s for s in settle if s.get('name')=='nextHp')['value']=op('select',condition=b('ge',v('hp'),v('amount')),then=sub(v('hp'),v('amount')),**{'else':lit(0)})
            if mutant==2:
                settle=copy.deepcopy(settle)
                next(s for s in settle if s.get('name')=='nextHp')['value']=v('hp')
            active=[iff(neg(req('target','immune')),settle)]
        else:active=direct
        prefix=[let('early',req('target','immune'))] if mutant==3 else []
        return [iff(exists('target'),[iff(b('gt',v('amount'),lit(0)),prefix+[let('hp',req('target','hp')),iff(b('gt',v('hp'),lit(0)),active)])])]
    if task=='R1':
        reserve=[let('cost',lit(3 if mutant==3 else 2)),let('credits',req('wallet','credits')),iff(b('ge',v('credits'),v('cost')),[wr('wallet','credits',sub(v('credits'),v('cost'))),wr('ticket','reserved',lit(True)),emit('Reserved','wallet','ticket')],[emit('Insufficient','wallet','ticket')])]
        original=[iff(neg(req('ticket','reserved')),reserve)]
        cancel=[emit('CancelUnavailable','ticket')]
        if new:
            released=[wr('wallet','credits',plus(v('credits'),lit(2)))]+([] if mutant==1 else [wr('ticket','reserved',lit(False))])+[emit('Released','wallet','ticket')]
            decision=released if mutant==2 else [iff(b('le',v('credits'),lit(8)),released,[emit('ReleaseDenied','wallet','ticket')])]
            cancel=[iff(req('ticket','reserved'),[let('credits',req('wallet','credits'))]+decision)]
        return [iff(exists('wallet','ticket'),[iff(v('cancel'),cancel,original)])]
    if task=='R2':
        count=v('steps') if new and mutant!=1 else lit(1)
        amount=op('select',condition=b('ge',v('remaining'),count),then=sub(v('remaining'),count),**{'else':lit(0)})
        settle=[let('remaining',req('machine','remaining')),let('nextRemaining',amount),wr('machine','remaining',v('nextRemaining')),iff(b('eq',v('nextRemaining'),lit(0)),[wr('machine','done',lit(True)),emit('Built','machine')])]
        if new:
            settle=[iff(b('gt',v('steps'),lit(0)),[iff(neg(req('machine','paused')),settle)])]
        if mutant==2:settle=[let('early',req('machine','paused'))]+settle
        active=[iff(neg(req('machine','done')),settle)]
        if mutant==3:active=[let('early',req('machine','paused'))]+active
        return [iff(exists('machine'),active)]
    raise ValueError('Unknown authored task')

def ir(task,version,mutant=0):
    program=abi(task);program['handlers'][0]['body']=ir_body(task,version,mutant);return program

# Native JS is authored separately; it does not interpret or wrap IR.
JS0={
'M1':"""export function onOpen(ctx, {buyer, item}) {
  if (!ctx.exists(buyer) || !ctx.exists(item)) return;
  if (ctx.require(ctx.read(item, 'sold'))) return;
  const cost = 2;
  const coins = ctx.require(ctx.read(buyer, 'coins'));
  if (coins < cost) { ctx.emit('PurchaseDenied', {buyer, item}); return; }
  ctx.write(buyer, 'coins', ctx.sub(coins, cost));
  ctx.write(item, 'sold', true);
  ctx.emit('Purchased', {buyer, item});
}
""",
'M2':"""export function onOpen(ctx, {oldGear, newGear}) {
  if (!ctx.exists(oldGear) || !ctx.exists(newGear)) return;
  if (oldGear[0] === newGear[0] && oldGear[1] === newGear[1] && oldGear[2] === newGear[2] && oldGear[3] === newGear[3]) return;
  if (ctx.require(ctx.read(newGear, 'equipped'))) return;
  if (ctx.require(ctx.read(newGear, 'locked'))) { ctx.emit('EquipDenied', {newGear}); return; }
  ctx.write(newGear, 'equipped', true);
  ctx.emit('Equipped', {oldGear, newGear});
}
""",
'C1':"""export function onOpen(ctx, {player, quest}) {
  if (!ctx.exists(player) || !ctx.exists(quest)) return;
  if (!ctx.require(ctx.read(quest, 'completed'))) return;
  const credits = ctx.require(ctx.read(player, 'credits'));
  if (credits >= 10) { ctx.emit('RewardBlocked', {player, quest}); return; }
  const nextCredits = ctx.sub(credits, ctx.sub(0, 1));
  ctx.write(player, 'credits', nextCredits);
  ctx.emit('Rewarded', {player, quest});
}
""",
'C2':"""export function onOpen(ctx, {target, amount}) {
  if (!ctx.exists(target) || amount === 0) return;
  const hp = ctx.require(ctx.read(target, 'hp'));
  if (hp === 0) return;
  const nextHp = hp >= amount ? ctx.sub(hp, amount) : 0;
  ctx.write(target, 'hp', nextHp);
  ctx.emit('Hit', {target});
  if (nextHp === 0) ctx.emit('Died', {target});
}
""",
'R1':"""export function onOpen(ctx, {wallet, ticket, cancel}) {
  if (!ctx.exists(wallet) || !ctx.exists(ticket)) return;
  if (cancel) { ctx.emit('CancelUnavailable', {ticket}); return; }
  if (ctx.require(ctx.read(ticket, 'reserved'))) return;
  const cost = 2;
  const credits = ctx.require(ctx.read(wallet, 'credits'));
  if (credits < cost) { ctx.emit('Insufficient', {wallet, ticket}); return; }
  ctx.write(wallet, 'credits', ctx.sub(credits, cost));
  ctx.write(ticket, 'reserved', true);
  ctx.emit('Reserved', {wallet, ticket});
}
""",
'R2':"""export function onOpen(ctx, {machine, steps}) {
  if (!ctx.exists(machine)) return;
  if (ctx.require(ctx.read(machine, 'done'))) return;
  const remaining = ctx.require(ctx.read(machine, 'remaining'));
  const nextRemaining = remaining >= 1 ? ctx.sub(remaining, 1) : 0;
  ctx.write(machine, 'remaining', nextRemaining);
  if (nextRemaining === 0) { ctx.write(machine, 'done', true); ctx.emit('Built', {machine}); }
}
"""}

def replace(source,old,new):
    if source.count(old)!=1:raise ValueError('Authored substitution must be unique')
    return source.replace(old,new)

def js(task,version,mutant=0):
    s=JS0[task]
    if version==0:return s
    if task=='M1':
        s=replace(s,"  if (coins < cost)","  const tokens = ctx.require(ctx.read(buyer, 'tokens'));\n  if (coins < cost || tokens < 1)")
        s=replace(s,"  ctx.write(item, 'sold', true);","  ctx.write(buyer, 'tokens', ctx.sub(tokens, 1));\n  ctx.write(item, 'sold', true);")
        if mutant==1:s=replace(s,"  ctx.write(buyer, 'tokens', ctx.sub(tokens, 1));\n",'')
        if mutant==2:s=replace(s,"  const tokens = ctx.require(ctx.read(buyer, 'tokens'));","  if (coins < cost) { ctx.emit('PurchaseDenied', {buyer, item}); return; }\n  const tokens = ctx.require(ctx.read(buyer, 'tokens'));")
        if mutant==3:s=replace(s,"  if (ctx.require(ctx.read(item, 'sold'))) return;","  ctx.require(ctx.read(buyer, 'coins'));\n  if (ctx.require(ctx.read(item, 'sold'))) return;")
    elif task=='M2':
        s=replace(s,"  ctx.write(newGear, 'equipped', true);","  if (!ctx.require(ctx.read(oldGear, 'equipped'))) { ctx.emit('EquipDenied', {newGear}); return; }\n  ctx.write(oldGear, 'equipped', false);\n  ctx.write(newGear, 'equipped', true);")
        if mutant==1:s=replace(s,"  ctx.write(oldGear, 'equipped', false);\n",'')
        if mutant==2:s=replace(s,"  if (oldGear[0] === newGear[0] && oldGear[1] === newGear[1] && oldGear[2] === newGear[2] && oldGear[3] === newGear[3]) return;\n",'')
        if mutant==3:s=replace(s,"  if (ctx.require(ctx.read(newGear, 'equipped'))) return;","  ctx.require(ctx.read(oldGear, 'equipped'));\n  if (ctx.require(ctx.read(newGear, 'equipped'))) return;")
    elif task=='C1':
        s=replace(s,"  if (!ctx.require(ctx.read(quest, 'completed'))) return;","  if (ctx.require(ctx.read(quest, 'claimed'))) return;\n  if (!ctx.require(ctx.read(quest, 'completed'))) return;\n  const reward = ctx.require(ctx.read(quest, 'reward'));")
        s=replace(s,"  if (credits >= 10) { ctx.emit('RewardBlocked', {player, quest}); return; }\n  const nextCredits = ctx.sub(credits, ctx.sub(0, 1));","  const nextCredits = ctx.sub(credits, ctx.sub(0, reward));\n  if (nextCredits > 10) { ctx.emit('RewardBlocked', {player, quest}); return; }")
        s=replace(s,"  ctx.emit('Rewarded', {player, quest});","  ctx.write(quest, 'claimed', true);\n  ctx.emit('Rewarded', {player, quest});")
        if mutant==1:s=replace(s,'ctx.sub(0, reward)','ctx.sub(0, 1)')
        if mutant==2:s=replace(s,"if (nextCredits > 10) { ctx.emit", "if (nextCredits > 10) { ctx.write(quest, 'claimed', true); ctx.emit")
        if mutant==3:s=replace(s,"  if (!ctx.require(ctx.read(quest, 'completed'))) return;","  ctx.require(ctx.read(player, 'credits'));\n  if (!ctx.require(ctx.read(quest, 'completed'))) return;")
    elif task=='C2':
        s=replace(s,"  const nextHp = hp >= amount ? ctx.sub(hp, amount) : 0;","  if (ctx.require(ctx.read(target, 'immune'))) return;\n  const shield = ctx.require(ctx.read(target, 'shield'));\n  const absorbed = shield >= amount ? amount : shield;\n  const damage = ctx.sub(amount, absorbed);\n  const nextShield = ctx.sub(shield, absorbed);\n  const nextHp = hp >= damage ? ctx.sub(hp, damage) : 0;\n  ctx.write(target, 'shield', nextShield);")
        if mutant==1:s=replace(s,'hp >= damage ? ctx.sub(hp, damage) : 0','hp >= amount ? ctx.sub(hp, amount) : 0')
        if mutant==2:s=replace(s,'hp >= damage ? ctx.sub(hp, damage) : 0','hp')
        if mutant==3:s=replace(s,'  if (hp === 0) return;',"  ctx.require(ctx.read(target, 'immune'));\n  if (hp === 0) return;")
    elif task=='R1':
        s=replace(s,"  if (cancel) { ctx.emit('CancelUnavailable', {ticket}); return; }", """  if (cancel) {
    if (!ctx.require(ctx.read(ticket, 'reserved'))) return;
    const credits = ctx.require(ctx.read(wallet, 'credits'));
    if (credits > 8) { ctx.emit('ReleaseDenied', {wallet, ticket}); return; }
    ctx.write(wallet, 'credits', ctx.sub(credits, ctx.sub(0, 2)));
    ctx.write(ticket, 'reserved', false);
    ctx.emit('Released', {wallet, ticket});
    return;
  }""")
        if mutant==1:s=replace(s,"    ctx.write(ticket, 'reserved', false);\n",'')
        if mutant==2:s=replace(s,"    if (credits > 8) { ctx.emit('ReleaseDenied', {wallet, ticket}); return; }\n",'')
        if mutant==3:s=replace(s,'  const cost = 2;','  const cost = 3;')
    elif task=='R2':
        s=replace(s,"  const remaining = ctx.require(ctx.read(machine, 'remaining'));","  if (steps === 0) return;\n  if (ctx.require(ctx.read(machine, 'paused'))) return;\n  const remaining = ctx.require(ctx.read(machine, 'remaining'));")
        s=replace(s,'remaining >= 1 ? ctx.sub(remaining, 1) : 0','remaining >= steps ? ctx.sub(remaining, steps) : 0')
        if mutant==1:s=replace(s,'remaining >= steps ? ctx.sub(remaining, steps) : 0','remaining >= 1 ? ctx.sub(remaining, 1) : 0')
        if mutant==2:s=replace(s,'  if (steps === 0) return;',"  ctx.require(ctx.read(machine, 'paused'));\n  if (steps === 0) return;")
        if mutant==3:s=replace(s,"  if (ctx.require(ctx.read(machine, 'done'))) return;","  ctx.require(ctx.read(machine, 'paused'));\n  if (ctx.require(ctx.read(machine, 'done'))) return;")
    return s

MUTANTS={'M1':[('N01','missing_token_debit'),('B01','skip_required_token_on_rejection'),('R02','sold_terminal_regression')],
 'M2':[('N01','old_equipment_not_disabled'),('B01','alias_priority_lost'),('R01','equipped_terminal_regression')],
 'C1':[('N01','fixed_reward_not_replaced'),('B01','claim_marked_when_denied'),('R01','incomplete_terminal_regression')],
 'C2':[('N02','shield_does_not_protect_hp'),('N03','remaining_damage_not_applied'),('R02','dead_terminal_regression')],
 'R1':[('N02','reservation_not_cleared'),('B01','refund_capacity_unchecked'),('R01','old_reserve_cost_changed')],
 'R2':[('N01','steps_ignored'),('N04','zero_step_priority_lost'),('R03','done_terminal_regression')]}

def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=1 if path.name.endswith('.ir.json') else 2)+'\n',encoding='utf-8',newline='\n')

def build():
    from .pilot24_oracles import cases, v0_cases
    design=(ROOT.parents[1]/'docs/pilot24/TASKS.md').read_text(encoding='utf-8')
    for task in TASKS:
        public=ROOT/'public'/task; private=ROOT/'private'/task
        public.mkdir(parents=True,exist_ok=True);private.mkdir(parents=True,exist_ok=True)
        section=design.split('## '+task+' ',1)[1].split('\n## ',1)[0]
        public_text=section.split('\n默认',1)[0]
        # C2 starts its default paragraph without the colon used by other tasks.
        (public/'requirements.md').write_text(('# '+task+' '+public_text).rstrip()+'\n',encoding='utf-8',newline='\n')
        write_json(public/'abi.json',abi(task));write_json(public/'starter.ir.json',ir(task,0))
        (public/'starter.js').write_text(js(task,0),encoding='utf-8',newline='\n')
        write_json(private/'cases.json',cases(task));write_json(private/'v0_cases.json',v0_cases(task))
        write_json(private/'control_v1.ir.json',ir(task,1))
        (private/'control_v1.js').write_text(js(task,1),encoding='utf-8',newline='\n')
        metadata=[]
        for i,(case_id,intent) in enumerate(MUTANTS[task],1):
            write_json(private/f'mutants/m{i}.ir.json',ir(task,1,i))
            (private/f'mutants/m{i}.js').write_text(js(task,1,i),encoding='utf-8',newline='\n')
            metadata.append({'index':i,'case_id':case_id,'intent':intent})
        write_json(private/'mutants/index.json',metadata)

if __name__=='__main__':build()
