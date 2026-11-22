# 一次修改失去响应后，怎样知道它是否提交？

假设库存原为第 7 版。一项请求要把它改到第 8 版，写入端却在答复之前退出。另一个写者随后又改出了第 9 版。此时“现在是什么值”“哪一项请求改变了第 7 版”“可以用第 8 版的计算结果吗”是三个不同的问题。仅仅读出最新值，不能证明第一项请求的结果。

本文比较的是 SQLite 的事务与约束、SQLAlchemy **2.0 ORM 的版本列**、Temporal 工作流、DBOS **Python** 工作流/事务，以及 Mutation Consistency Kit **0.1.1** 的本地文档替换。它们不在同一层。下列比较讲的是各自默认或明确配置的保证，不是性能基准、产品排名，也不主张任何能力为本库独有。

## 故障之后，分别能查到什么？

| 方案 | 同一修改再来一次 | 持久证据与响应丢失后的恢复 | revision 一致性 |
| --- | --- | --- | --- |
| SQLite 原生 | 应用可用请求键唯一约束绑定请求，并让重复请求读回原结果。普通事务或 UPSERT 本身不保证请求幂等；冲突时再做一次加法仍可能加两次。 | 把请求、结果与业务修改放在同一事务，可做出与本库同类的持久回执；若只存最新值，则无内置业务回执可查。 | 条件更新可核对预期版本，并检查影响行数。版本历史和旧输出的使用门禁由应用设计。[事务](https://sqlite.org/lang_transaction.html) · [UPSERT](https://sqlite.org/lang_upsert.html) |
| SQLAlchemy 版本列 | ORM 以版本条件保护行。重复使用过期版本可能得到冲突，而不是第一次成功的回执。要实现请求去重，可另建操作表。 | 单独使用版本列不会保存请求结果。可在同一 Session 事务里提交业务行和操作记录，故障后按键查回；只刷新 ORM 对象不足以辨认原请求。 | 映射的 `version_id_col` 在逐对象 flush 时检查 UPDATE/DELETE；行不匹配可抛出 `StaleDataError`。不能把该保护推广到批量 UPDATE/DELETE 或绕过 ORM 的写入。[版本计数器](https://docs.sqlalchemy.org/en/20/orm/versioning.html) |
| Temporal | 命名空间内的 Workflow ID 配合冲突/复用策略控制重复启动；它不是外部写入的幂等键。Activity 可能多次执行。 | 服务保存事件历史；Worker 可重放以恢复工作流。已记录的 Activity 完成结果可复用。若外部系统已提交、Activity 却没能报告完成，重试仍需同一业务键或外部回执核对。 | 工作流历史维护流程状态，不自动检查外部库存行的版本；在 Activity 的数据库操作中加条件更新即可组合两者。[Workflow ID](https://docs.temporal.io/workflow-execution/workflowid-runid) · [事件历史](https://docs.temporal.io/encyclopedia/event-history) · [Activity 幂等](https://docs.temporal.io/activity-definition#idempotency) |
| DBOS Python | 指定 Workflow ID 可去重。工作流中的受支持数据库事务可把业务写入与结果记录原子提交；这比只给普通步骤设重试更强。 | 中断后重放工作流。已提交的受管事务读取保存的结果，不再次执行；未被可靠记录的普通步骤可能重试。任意外部 HTTP 副作用不因此自动获得数据库事务的保证。 | 事务及其结果绑定的是步骤执行，不是特定文档版本。若业务要拒绝第 7 版以外的写入，仍可在事务内加入版本条件。[工作流保证](https://docs.dbos.dev/python/tutorials/workflow-tutorial) · [事务与数据源](https://docs.dbos.dev/python/tutorials/transaction-tutorial) |
| Mutation Kit | `replace` 将同一库内的操作键绑定到目标、预期版本和规范化正文。相同重放返回原回执或原未决状态；证据完整时，同键异载荷拒绝。初始化 `create` 不提供请求键。 | `recover(key)` 只读核对回执、版本和当前头，可在已出现第 9 版时确认某次请求确实产生第 8 版。只剩预留或证据矛盾可返回 UNKNOWN；查无记录是 None，读失败可能抛异常。都不应被当作重提写入的许可。 | 预期 revision 限制替换。`deliver` 在本地读取时拒绝过期 revision；不是外部发送，也不阻止检查返回后的新写入。[API](docs/API.md) · [事务与边界](docs/ARCHITECTURE.md) |

## 运行代价与选型

| 方案 | 部署和数据库依赖 | 何时优先考虑 |
| --- | --- | --- |
| SQLite 原生 | 通常嵌入进程，无单独数据库服务。开发方负责表结构、故障恢复和测试。 | 已有 SQLite 表，仅需一两处条件写入，或有特殊模式和保留策略。自己实现同类回执完全可行。[SQLite 的适用场景](https://sqlite.org/whentouse.html) |
| SQLAlchemy | 应用进程内的库，加数据库驱动及所选数据库；可用 SQLite，也能与已有服务型数据库协作。 | 原系统已有 ORM 模型，主要问题是两个会话覆盖同一行，不想换成整份 JSON 文档存储。[数据库方言](https://docs.sqlalchemy.org/en/20/dialects/) |
| Temporal | 生产使用 Temporal 服务（自管或 Cloud）及应用 Worker。自管需持久化后端，如 PostgreSQL、MySQL 或 Cassandra；服务端 SQLite 是开发/测试选项。业务数据库可与工作流存储分开。 | 有长时间等待、多个服务、重试和补偿的流程，团队需要恢复整个工作流，而非只读取某一次本地修改。[服务持久化](https://docs.temporal.io/temporal-service/persistence) |
| DBOS Python | 库嵌入应用，用系统数据库存流程状态；SQLite 可用于轻量本地运行，官方推荐生产使用 PostgreSQL。不要求为使用 DBOS 而另起专门编排服务。 | 应用需要多步恢复，特别是希望把受管数据库操作及其检查点一起提交。普通外部步骤仍要设计幂等。[接入方式](https://docs.dbos.dev/python/integrating-dbos) · [系统数据库配置](https://docs.dbos.dev/python/tutorials/database-connection/) |
| Mutation Kit | Python 标准库及本地文件型 SQLite，无额外第三方 Python 运行依赖。保存完整请求和逐版正文；历史越长，空间和核对成本越高。 | 只需回答“这次文档修改是否提交、旧结果还能否使用”，并希望未决请求保持未决、不被后台自动重做。多库、远端送达或有自动推进需求时，不宜把它当作工作流引擎。[存储假设与成本](docs/ARCHITECTURE.md) |

**选择标准：要恢复的是一份业务回执，还是整个执行流程？** 本库的取舍是把前者封装成小型本地 API。原生 SQL 可以实现同一策略，ORM 可以扩大现有模型的并发保护，两种工作流方案则解决本库刻意不做的跨步骤恢复。没有在同一硬件和负载下实测这些方案，本文不作速度、成本或故障率排名。
