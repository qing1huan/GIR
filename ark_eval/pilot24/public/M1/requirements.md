# M1 多资源商店购买（多步修改）

**ABI：** `Open:{buyer:EntityID,item:EntityID}`。字段 `coins:int[0,10]`、`tokens:int[0,10]`、`sold:bool`。输出 `Purchased:{buyer,item}`、`PurchaseDenied:{buyer,item}`，成员均EntityID。

**等价v0 starter：** 先确认buyer/item均存在，否则正常无效果；require(item.sold)，已售则返回且不要求余额cell；未售require(buyer.coins)，coins≥2则扣2、sold=true、发Purchased一次，否则发PurchaseDenied。tokens字段已在ABI，但v0忽略。IR和JS都完整实现这些旧行为，成本用具名常量，不埋故障或TODO。

**v1新规则：** 未售路径必须先取得coins和tokens两个必需值，再共同判定coins≥2且tokens≥1；成功同时扣2 coins和1 token、sold=true、发Purchased，否则只发PurchaseDenied。即使coins不足，缺tokens也属于I，因为两个余额均为本路径必需值。保留存在性、已售短路、coins不足正常拒绝、重复购买幂等。buyer=item允许，三个不同名字的cell正常更新。
