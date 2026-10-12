# R2 生产倒计时新增暂停和批量步进（回归风险）

**ABI：** `Open:{machine:EntityID,steps:int}`，任务调用域steps=0..3。字段 `remaining:int[0,10],done:bool,paused:bool`。输出 `Built:{machine}`。

**等价v0 starter：** machine存在；require(done)，true返回；false则require(remaining)，每次固定减1且下界0，写remaining；结果0时done=true并发Built一次。原先忽略paused/steps；二者已在头/参数中，不造成隐藏ABI变更。

**v1新规则：** 存在性后仍先require(done)，true直接返回；未完成且steps=0直接返回，不要求paused/remaining；否则require(paused)，true返回且不要求remaining；false才require(remaining)，按steps减少并饱和到0。结果0时done=true并发Built；后续终态请求不重复。这里明确添加小范围steps参数语义，使修改至少涉及短路、步数和边界三处，而不只是加一个if。
