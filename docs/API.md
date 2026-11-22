# Python API

公开名称从 `mutation_consistency_kit` 导入。包名与 Python 导入名分别使用短横线和下划线。

| 调用 | 返回与约束 |
| --- | --- |
| `Store(path)` | 打开或创建本地文件库。父目录须存在；拒绝内存库、未知库或格式版本。每次操作自行关闭连接。 |
| `create(target, value)` | 初始化目标并返回版本 1 的 `Snapshot`；重复初始化抛出 `TargetExistsError`。初始化是独立的单库事务，没有请求键。 |
| `read(target)` | 返回已核对的 `Snapshot`。目标缺失抛出 `KeyError`，数据证据矛盾抛出 `InconsistentStoreError`。 |
| `replace(key, target, expected_revision, value)` | 完整替换文档并返回 `Receipt`。键在整个库内唯一。已绑定的同一键配不同目标、预期版本或正文，且原证据一致时抛出 `KeyReuseError`。 |
| `recover(key)` | 只读核对并返回原 `Receipt`；键及其版本链接都不存在时返回 `None`。读取操作无法执行时传播数据库异常，调用方应保留待核对状态。 |
| `deliver(target, revision, output)` | 重新核对目标的当前版本并返回传入的本地输出。缺失或过期抛出 `StaleRevisionError`；证据矛盾抛出 `InconsistentStoreError`。调用方负责将输出绑定到实际用于计算的那份快照。 |

键和目标使用非空、非纯空白的 Unicode 字符串；预期版本须为正的 SQLite 有符号整数，布尔值无效。正文为 JSON 对象，支持字符串键、嵌套对象、数组、有限数值、布尔值和空值。拒绝循环结构、元组、非字符串键和孤立代理码位。成员按键排序，数组顺序保留；例如数值 `1` 和 `1.0` 的编码不同。非法输入一般产生 `TypeError` 或 `ValueError`；极深嵌套还受 Python 递归上限约束。传入后应保持对象稳定，返回的快照内容可本地编辑，只有显式 `replace` 会修改存储。

## 回执字段

| 字段 | 意义 |
| --- | --- |
| `key`、`target` | 操作身份和目标身份 |
| `expected_revision` | 调用者提出的原版条件；请求记录损坏且只剩版本链接时可能为 `None` |
| `previous_revision` | 成功时为修改前的实际版本；冲突时为裁决时看到的实际版本；目标缺失或结果不明确时为 `None` |
| `result_revision` | 已核实成功写入的版本；其他状态为 `None` |
| `status` | `Status.SUCCEEDED`、`Status.FAILED` 或 `Status.UNKNOWN` |
| `reason` | 已提交为 `applied`；已拒绝为 `revision_conflict` 或 `target_missing`；未决为 `reserved`、`storage_unconfirmed` 或 `inconsistent_evidence` |

失败回执表明该键的业务请求被明确拒绝。调用者若决定基于新事实提出另一个修改，应先重新读取文档，再为新意图生成新键。收到 `UNKNOWN` 后保留原键，使用只读 `recover`。如果收到 `storage_unconfirmed` 且库内没有预留，人工再次发出 `replace` 可以创建一项新预留，因此不要让调用层自动重提或更换键。当前 API 没有将未决请求强制标为成功、失败或重新执行的操作。
