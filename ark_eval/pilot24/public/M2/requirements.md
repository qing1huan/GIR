# M2 装备替换（多步修改）

**ABI：** `Open:{oldGear:EntityID,newGear:EntityID}`。字段 `equipped:bool,locked:bool`。输出 `Equipped:{oldGear,newGear}`、`EquipDenied:{newGear}`。

**等价v0 starter：** 两目标存在，否则无效果；EntityID相同则立即无效果，不要求任何cell。不同目标require(newGear.equipped)，已装备则返回；否则require(newGear.locked)，locked=true发EquipDenied；未锁定则newGear.equipped=true并发Equipped。v0只保证新装备激活，不读取/修改oldGear.equipped。

**v1新规则：** 未锁定且新装备尚未激活时，另require(oldGear.equipped)。旧装备未激活只发EquipDenied；已激活则先取消oldGear.equipped、再激活newGear.equipped，并发Equipped一次。保留同实体no-op、新装备终态短路、锁定优先拒绝；locked=true时不需要oldGear.equipped，不能把缺cell变成错误。
