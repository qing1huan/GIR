# C2 护盾与伤害结算（跨规则约束）

**ABI：** `Open:{target:EntityID,amount:int}`，任务调用域amount=0..4。字段 `hp:int[0,10],shield:int[0,10],immune:bool`。输出 `Hit:{target}`、`Died:{target}`。

**等价v0 starter：** target不存在或amount=0正常返回；然后require(hp)，hp=0返回；否则hp降至max(0,hp-amount)，发Hit，若降至0再发Died。不读取immune/shield。最大/最小用分支和checked sub表达，不依赖新API。

**v1新规则：** 保留存在性、amount=0、hp=0优先级；hp>0时require(immune)，true返回且不要求shield；false时require(shield)。先用护盾吸收，剩余伤害才扣hp，均不低于0；随后按新值写shield与hp。只要进入这次有效伤害结算就发Hit（全被护盾吸收也发）；hp降至0时再发Died。amount=0不发Hit；已死亡不重复发Died。无需额外攻击者对象。
