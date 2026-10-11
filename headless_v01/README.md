# Headless Game IR v0.1 参考实现

使用Python3.12标准库。安装与Windows UTF-8配置见根目录 [MIGRATION.md](../MIGRATION.md)。
从仓库根目录执行：

```bash
python -m unittest discover -s headless_v01/tests -t . -v
python -m headless_v01.verify --output ./results/headless-local
python -m headless_v01 run headless_v01/cases/PT11.json --output ./results/death
python -m headless_v01 inspect ./results/death/snapshot.json
python -m headless_v01 replay headless_v01/cases/PT11.json --journal ./results/death/journal.json --output ./results/death-replay
```

正常提交exit0；预期ABORTED反例PT14/PT15为exit2。CLI支持run/step/inspect/replay。
run产出Core/Plan、snapshot、driver、journal和results；失败driver必须随snapshot保存，
只允许使用原冻结输入进行显式retry。`step --snapshot BASE.json --request REQUEST.json`
恢复后执行一个tick；`replay --snapshot BASE.json --journal SEGMENT.json`验证后续段。

## 实现范围

Semantic库Door@1/Health@1展开到typed Core；其余玩法用显式CoreOverride AST。
Core无任意Python/JS callback或Any类型。支持closed值、显式权威/read/observe/effect、
每事件microstep、S_m快照与own overlay、Root原子回滚、冲突策略、一次clamp、确定性
RNG/IDs、未来事件、预算和完整逻辑回放。配置/schema/capability版本在加载时检查。

query支持typed谓词；map/filter和nearest坐标读取均显式，等距按EntityID。
constant_fold@1 Plan保留原Core的预算与source顺序；CoreOverride再展开检查冲突。
Lifecycle提供typed实例namespace、preserve/rebase/restart、纯迁移AST与全批回滚。
SemanticEditor提供draft/validate/commit、typed事件端口、revision、保守impact和rollback。
ABI不兼容或pending future编辑要求显式迁移；这不是完整生产编辑器。
ScriptedBackend仅为mock，不代表真实Nav/Physics adapter。

## 测试与限制

63个唯一工程方法包含12个端到端fixture。去重映射见coverage.py及公开证据目录；
重复CLI/trace生成、fixture序列化检查和impact标签不增加实际执行方法数。
Core/Plan共享解释器，不能冒充独立引擎对照。A09/A12完整验收未运行，不正式冻结，
被测AI运行0次，指标null。cases与tests是已知开发集。
