# 六题共享规格、starter语义与oracle设计

状态：设计数据，尚无可执行starter、参考实现或oracle JSON。仅供评测作者/Sol，不得整体发给受测模型；尤其所有N/B/R表与故障控制均属私有评分设计。公共请求只提取文字业务规格、v0/v1规则和自己表示的starter。

共同执行、错误、别名、预算与评分规则见[DESIGN.md](DESIGN.md)。六题均用唯一Open事件/handler open/导出onOpen作为传输入口，业务名独立。以下字段均IR/exclusive；表列int范围、其余为bool，实体上允许缺cell。输入/输出事件payload都是闭合record；所有题另声明Tick:{}。services和extensions为空。未列字段不变，未列事件不发，commands=[]。

测试记法：角色A/B/C使用三个固定、不同的EntityID `["world",-1,"genesis",0/1/2]`；X=`["world",-1,"genesis",99]`不存在。每行都从独立默认世界开始，只应用该行覆盖。`缺f`指删除cell，不是false/0。箭头后给出完整变化集和按序事件，其他值不变；“无变化”均COMMITTED。`I`=ABORTED/InvalidEntity并恢复本tick开始状态、发布事件=[]、commands=[]。同tick两个输入的整tick回滚按共同规则；跨tick明确写`t0/t1`。seed固定1。Sol必须把这些设计展开成逐字段完整expected，不能用运行输出填expected。

## M1 多资源商店购买（多步修改）

**ABI：** `Open:{buyer:EntityID,item:EntityID}`。字段 `coins:int[0,10]`、`tokens:int[0,10]`、`sold:bool`。输出 `Purchased:{buyer,item}`、`PurchaseDenied:{buyer,item}`，成员均EntityID。

**等价v0 starter：** 先确认buyer/item均存在，否则正常无效果；require(item.sold)，已售则返回且不要求余额cell；未售require(buyer.coins)，coins≥2则扣2、sold=true、发Purchased一次，否则发PurchaseDenied。tokens字段已在ABI，但v0忽略。IR和JS都完整实现这些旧行为，成本用具名常量，不埋故障或TODO。

**v1新规则：** 未售路径必须先取得coins和tokens两个必需值，再共同判定coins≥2且tokens≥1；成功同时扣2 coins和1 token、sold=true、发Purchased，否则只发PurchaseDenied。即使coins不足，缺tokens也属于I，因为两个余额均为本路径必需值。保留存在性、已售短路、coins不足正常拒绝、重复购买幂等。buyer=item允许，三个不同名字的cell正常更新。

默认：buyer A={coins:5,tokens:3}，item B={sold:false}；输入Open(A,B)。

| ID/组 | 初始覆盖或输入 | 固定v1期望 |
|---|---|---|
| N01 | 默认 | A=(3,2)，B.sold=true；Purchased(A,B) |
| N02 | A=(coins:2,tokens:1) | A=(0,0)，B.sold=true；Purchased(A,B) |
| N03 | A.tokens=0 | 无变化；PurchaseDenied(A,B) |
| N04 | 默认，同tick两次Open(A,B) | 只扣一次，A=(3,2)，B.sold=true；Purchased(A,B)一次 |
| B01 | A.coins=0，缺A.tokens | I，不能先发余额不足事件 |
| B02 | 缺B.sold | I |
| B03 | 只有A={coins:5,tokens:3,sold:false}，Open(A,A) | A=(3,2,true)；Purchased(A,A) |
| B04 | 默认另有C={}；同tick Open(A,B),Open(A,C) | I；A=(5,3)，B.sold=false，C仍空，事件清空 |
| R01 | A.coins=1，tokens仍3 | 无变化；PurchaseDenied(A,B) |
| R02 | B.sold=true，缺A.coins与tokens | 无变化/无事件 |
| R03 | buyer=X，B={} | 无变化/无事件 |
| R04 | item=X，A={} | 无变化/无事件 |

故障意图：只加tokens门槛却漏扣tokens→N01；coins不足时跳过必需tokens→B01；提前require余额破坏已售短路→R02。

## M2 装备替换（多步修改）

**ABI：** `Open:{oldGear:EntityID,newGear:EntityID}`。字段 `equipped:bool,locked:bool`。输出 `Equipped:{oldGear,newGear}`、`EquipDenied:{newGear}`。

**等价v0 starter：** 两目标存在，否则无效果；EntityID相同则立即无效果，不要求任何cell。不同目标require(newGear.equipped)，已装备则返回；否则require(newGear.locked)，locked=true发EquipDenied；未锁定则newGear.equipped=true并发Equipped。v0只保证新装备激活，不读取/修改oldGear.equipped。

**v1新规则：** 未锁定且新装备尚未激活时，另require(oldGear.equipped)。旧装备未激活只发EquipDenied；已激活则先取消oldGear.equipped、再激活newGear.equipped，并发Equipped一次。保留同实体no-op、新装备终态短路、锁定优先拒绝；locked=true时不需要oldGear.equipped，不能把缺cell变成错误。

默认：oldGear A={equipped:true}，newGear B={equipped:false,locked:false}；Open(A,B)。

| ID/组 | 初始覆盖或输入 | 固定v1期望 |
|---|---|---|
| N01 | 默认 | A.equipped=false，B.equipped=true；Equipped(A,B) |
| N02 | A.equipped=false | 无变化；EquipDenied(B) |
| N03 | 缺A.equipped | I |
| N04 | 默认，同tick两次Open(A,B) | A=false、B=true；Equipped(A,B)一次 |
| B01 | 只有A={}，Open(A,A) | 无变化/无事件，别名优先于cell读取 |
| B02 | B.locked=true，缺A.equipped | 无变化；EquipDenied(B) |
| B03 | 默认另有C={}；同tick Open(A,B),Open(B,C) | I；恢复A=true、B=false，事件清空 |
| B04 | 缺B.locked，B.equipped=false | I |
| R01 | B.equipped=true，缺B.locked与A.equipped | 无变化/无事件 |
| R02 | oldGear=X，B={} | 无变化/无事件 |
| R03 | newGear=X | 无变化/无事件 |
| R04 | B.locked=true，其余默认 | 无变化；EquipDenied(B) |

三个必需故障意图：漏取消旧装备→N01；别名时继续require→B01；终态前require旧cell→R01。锁定前require旧装备→B02可另做工程控制，不改变主评分集合。

## C1 任务领奖（跨规则约束）

**ABI：** `Open:{player:EntityID,quest:EntityID}`。字段 `credits:int[0,10]`、`reward:int[1,3]`、`completed:bool,claimed:bool`。输出 `Rewarded:{player,quest}`、`RewardBlocked:{player,quest}`。

**等价v0 starter：** 两目标存在；require(quest.completed)，false返回；true则require(player.credits)。credits<10时增加固定1并发Rewarded，否则发RewardBlocked。v0是可重复的小奖励，忽略claimed/reward；两臂使用同一checked整数加法语义。

**v1新规则：** 存在性后先require(claimed)，true立即返回，不要求completed/reward/credits；false再require(completed)，未完成返回，不要求reward/credits；已完成则require(reward)与credits，两个值都取得后判定credits+reward≤10。超容量只发RewardBlocked且claimed仍false；可容纳则增加reward、claimed=true、发Rewarded一次。检查过的加法用现有sub组合表达；不是新增宿主操作。player=quest允许，四个不同名cell共存即可。

默认：player A={credits:3}，quest B={completed:true,claimed:false,reward:2}；Open(A,B)。

| ID/组 | 初始覆盖或输入 | 固定v1期望 |
|---|---|---|
| N01 | 默认 | A.credits=5，B.claimed=true；Rewarded(A,B) |
| N02 | 默认，t0/t1各Open(A,B) | t0变为(5,true)并Rewarded一次；t1无新增变化/事件，两tick均COMMITTED |
| N03 | B.claimed=true，缺B.completed/reward及A.credits | 无变化/无事件 |
| N04 | A.credits=7，B.reward=3 | credits=10、claimed=true；Rewarded(A,B) |
| B01 | A.credits=8，B.reward=3 | 无变化；RewardBlocked(A,B)，claimed保持false |
| B02 | 缺B.claimed，B.completed=false | I |
| B03 | 缺B.reward，其他默认 | I |
| B04 | 默认另有C={completed:true,claimed:false}；同tick Open(A,B),Open(A,C) | I；恢复A.credits=3、B.claimed=false，事件清空 |
| R01 | B.completed=false且claimed=false，缺B.reward及A.credits | 无变化/无事件 |
| R02 | player=X，B={} | 无变化/无事件 |
| R03 | quest=X | 无变化/无事件 |
| R04 | A.credits=10，B.reward=1 | 无变化；RewardBlocked(A,B) |

三个必需故障意图：仍加固定1→N01；容量拒绝却把claimed置true→B01；未完成时提前require余额→R01。已领后仍require奖励/余额→N03可另做工程控制。R01/R04验证旧版未完成/容量拒绝行为保留；不得要求旧版成功发奖后的claimed字段与新版相同。

## C2 护盾与伤害结算（跨规则约束）

**ABI：** `Open:{target:EntityID,amount:int}`，任务调用域amount=0..4。字段 `hp:int[0,10],shield:int[0,10],immune:bool`。输出 `Hit:{target}`、`Died:{target}`。

**等价v0 starter：** target不存在或amount=0正常返回；然后require(hp)，hp=0返回；否则hp降至max(0,hp-amount)，发Hit，若降至0再发Died。不读取immune/shield。最大/最小用分支和checked sub表达，不依赖新API。

**v1新规则：** 保留存在性、amount=0、hp=0优先级；hp>0时require(immune)，true返回且不要求shield；false时require(shield)。先用护盾吸收，剩余伤害才扣hp，均不低于0；随后按新值写shield与hp。只要进入这次有效伤害结算就发Hit（全被护盾吸收也发）；hp降至0时再发Died。amount=0不发Hit；已死亡不重复发Died。无需额外攻击者对象。

默认target A={hp:5,shield:2,immune:false}，amount=3。

| ID/组 | 初始覆盖或输入 | 固定v1期望 |
|---|---|---|
| N01 | immune=true，缺shield | 无变化/无事件 |
| N02 | shield=5 | hp=5、shield=2；Hit(A) |
| N03 | shield=1 | hp=3、shield=0；Hit(A) |
| N04 | hp=2、shield=1 | hp=0、shield=0；Hit(A),Died(A) |
| B01 | shield=3 | hp=5、shield=0；Hit(A) |
| B02 | 缺immune，hp仍5 | I |
| B03 | immune=false，缺shield | I |
| B04 | 默认另有B={hp:5,immune:false}；同tick伤害A再伤害B，amount均3 | I；A恢复(5,2,false)，发布事件清空 |
| R01 | shield=0、immune=false、amount=2 | hp=3、shield=0；Hit(A)，与旧版相同 |
| R02 | hp=0，缺immune/shield，amount=3 | 无变化/无事件 |
| R03 | A={}，amount=0 | 无变化/无事件 |
| R04 | target=X，amount=3 | 无变化/无事件 |

故障意图：仍直接扣全部hp→N02；护盾不足时漏扣剩余伤害→N03；先读immune破坏hp=0短路→R02。B04独立检验整tick回滚，不能从吞掉第二次错误后的部分成功构造expected。

## R1 资源预留新增取消（回归风险）

**ABI：** `Open:{wallet:EntityID,ticket:EntityID,cancel:bool}`。字段 `credits:int[0,10],reserved:bool`。输出 `Reserved:{wallet,ticket}`、`Insufficient:{wallet,ticket}`、`Released:{wallet,ticket}`、`ReleaseDenied:{wallet,ticket}`、`CancelUnavailable:{ticket}`。

**等价v0 starter：** 两实体必须存在，否则无效果。cancel=true只发CancelUnavailable，不读任何cell；cancel=false时require(reserved)，已预留返回且不要求credits；否则require(credits)，至少2则扣2、reserved=true并发Reserved，不足只发Insufficient。公共常量amount=2。两臂都实现完整旧流程，不把取消分支留成TODO。

**v1新规则：** 保留cancel=false的全部行为。cancel=true改为require(reserved)，false正常返回且不要求credits；true再require(credits)。若credits≤8，则返还2、reserved=false、发Released；否则只发ReleaseDenied，保留reserved=true，不依赖越界写入触发错误来表示业务拒绝。已取消不能再次返还。wallet=ticket允许，两个字段互不重名。

默认wallet A={credits:4}，ticket B={reserved:false}，cancel=false。

| ID/组 | 初始覆盖或输入 | 固定v1期望 |
|---|---|---|
| N01 | B.reserved=true，cancel=true | credits=6、reserved=false；Released(A,B) |
| N02 | 同N01，同tick两次cancel=true | credits=6、reserved=false；Released(A,B)一次 |
| N03 | credits=8、reserved=true、cancel=true | credits=10、reserved=false；Released(A,B) |
| N04 | 默认，同tick cancel=false再true | 最终credits=4、reserved=false；Reserved(A,B),Released(A,B) |
| B01 | credits=9、reserved=true、cancel=true | 无变化；ReleaseDenied(A,B)，reserved仍true |
| B02 | 缺B.reserved，cancel=true | I |
| B03 | reserved=false，缺A.credits，cancel=true | 无变化/无事件 |
| B04 | N01初态，另有C={}；同tick取消B再取消C | I；恢复credits=4、B.reserved=true，事件清空 |
| R01 | 默认 | credits=2、reserved=true；Reserved(A,B) |
| R02 | credits=1，cancel=false | 无变化；Insufficient(A,B) |
| R03 | reserved=true，缺credits，cancel=false | 无变化/无事件 |
| R04 | ticket=X，A={}，cancel=false | 无变化/无事件 |

故障意图：返还后不清reserved→N02；不处理容量上界直接写入11→B01；改取消时错误改变原扣额/预留行为→R01。取消模式取代CancelUnavailable属于明确新规则，不能将该变化计成回归。

## R2 生产倒计时新增暂停和批量步进（回归风险）

**ABI：** `Open:{machine:EntityID,steps:int}`，任务调用域steps=0..3。字段 `remaining:int[0,10],done:bool,paused:bool`。输出 `Built:{machine}`。

**等价v0 starter：** machine存在；require(done)，true返回；false则require(remaining)，每次固定减1且下界0，写remaining；结果0时done=true并发Built一次。原先忽略paused/steps；二者已在头/参数中，不造成隐藏ABI变更。

**v1新规则：** 存在性后仍先require(done)，true直接返回；未完成且steps=0直接返回，不要求paused/remaining；否则require(paused)，true返回且不要求remaining；false才require(remaining)，按steps减少并饱和到0。结果0时done=true并发Built；后续终态请求不重复。这里明确添加小范围steps参数语义，使修改至少涉及短路、步数和边界三处，而不只是加一个if。

默认machine A={remaining:5,done:false,paused:false}，steps=1。

| ID/组 | 初始覆盖或输入 | 固定v1期望 |
|---|---|---|
| N01 | steps=3 | remaining=2、done=false；无事件 |
| N02 | remaining=2、steps=3 | remaining=0、done=true；Built(A) |
| N03 | paused=true，缺remaining，steps=2 | 无变化/无事件 |
| N04 | done=false，缺paused/remaining，steps=0 | 无变化/无事件 |
| B01 | remaining=0、steps=2 | remaining=0、done=true；Built(A) |
| B02 | 缺paused，steps=1 | I |
| B03 | paused=false，缺remaining，steps=1 | I |
| B04 | 默认另有B={remaining:5,done:false}；同tick推进A再推进B，steps均1 | I；A恢复remaining=5、done=false，事件清空 |
| R01 | 默认 | remaining=4、done=false；无事件 |
| R02 | remaining=1、steps=1 | remaining=0、done=true；Built(A) |
| R03 | done=true，缺remaining/paused，steps=3 | 无变化/无事件 |
| R04 | machine=X，steps=3 | 无变化/无事件 |

故障意图：仍固定减1→N01；零步数仍require paused→N04；把done判断移到新字段之后→R03。R02保留旧版完成边界；N02是合法修改后的批量完成行为，两者不能混为同一测试结论。

## Oracle制作与可冻结条件

72行只是设计场景，不能作为“已跑72项测试”报告。Sol为每题生成`cases.json`时，手工逐字段展开initial、每tick inputs、expected.entities/statuses/events/commands/errors；清晰区分两个输入同tick与两个tick。保留表中12个ID和N/B/R分组，不能用新观察随意增删主分数场景。

每题还需一份私有v0契约控制集合，足够证明公共starter不是坏起点；R01–R04在相同输入下必须与v1有相同可观察结果。每题的三个必需故障意图要在IR/JS中各实现一次；每题均含一个R组能检出的旧行为回归变种。额外工程控制不改变模型评分案例。

任一文本期望与Core实际已定义语义不一致时，先标为设计阻断并提交复审；不要让IR或JS某个控制自动决定oracle，也不能改核心runtime迎合表格。公共v0程序和私有v1控制相互独立校验，原生JS必须有真实可信控制执行证据，不能只把Python ctx模拟通过冒充JS通过。全部材料和可信测试完成前仍不得声明材料已冻结、模型调用可启动或新候选可执行。
