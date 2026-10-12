# C1 任务领奖（跨规则约束）

**ABI：** `Open:{player:EntityID,quest:EntityID}`。字段 `credits:int[0,10]`、`reward:int[1,3]`、`completed:bool,claimed:bool`。输出 `Rewarded:{player,quest}`、`RewardBlocked:{player,quest}`。

**等价v0 starter：** 两目标存在；require(quest.completed)，false返回；true则require(player.credits)。credits<10时增加固定1并发Rewarded，否则发RewardBlocked。v0是可重复的小奖励，忽略claimed/reward；两臂使用同一checked整数加法语义。

**v1新规则：** 存在性后先require(claimed)，true立即返回，不要求completed/reward/credits；false再require(completed)，未完成返回，不要求reward/credits；已完成则require(reward)与credits，两个值都取得后判定credits+reward≤10。超容量只发RewardBlocked且claimed仍false；可容纳则增加reward、claimed=true、发Rewarded一次。检查过的加法用现有sub组合表达；不是新增宿主操作。player=quest允许，四个不同名cell共存即可。
