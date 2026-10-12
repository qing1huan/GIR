# R1 资源预留新增取消（回归风险）

**ABI：** `Open:{wallet:EntityID,ticket:EntityID,cancel:bool}`。字段 `credits:int[0,10],reserved:bool`。输出 `Reserved:{wallet,ticket}`、`Insufficient:{wallet,ticket}`、`Released:{wallet,ticket}`、`ReleaseDenied:{wallet,ticket}`、`CancelUnavailable:{ticket}`。

**等价v0 starter：** 两实体必须存在，否则无效果。cancel=true只发CancelUnavailable，不读任何cell；cancel=false时require(reserved)，已预留返回且不要求credits；否则require(credits)，至少2则扣2、reserved=true并发Reserved，不足只发Insufficient。公共常量amount=2。两臂都实现完整旧流程，不把取消分支留成TODO。

**v1新规则：** 保留cancel=false的全部行为。cancel=true改为require(reserved)，false正常返回且不要求credits；true再require(credits)。若credits≤8，则返还2、reserved=false、发Released；否则只发ReleaseDenied，保留reserved=true，不依赖越界写入触发错误来表示业务拒绝。已取消不能再次返还。wallet=ticket允许，两个字段互不重名。
