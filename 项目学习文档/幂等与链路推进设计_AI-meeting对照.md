# SQLBot 幂等与链路推进设计：AI-meeting 源码对照

日期：2026-09-11。状态：设计草案，尚未实现或部署。关联：[七项开发计划](D:/SQLBot-main/项目学习文档/后端稳定性开发计划.md)。

## 1. 研究范围与结论

参考代码位于 `D:/AI-Meeting`。从 `POST /api/xunzhi/v1/interview/sessions/{sessionId}/interview/answer` 追踪普通回答链路及主要保护层。SQLBot 以 `D:/SQLBot-main` 工作区为依据；没有修改 AI-meeting，也没有运行其 Java 测试或真实 Redis 故障实验。

推荐借鉴“身份校验 → 幂等回放 → 执行权保护 → 状态复核 → 模型计算 → 确定性提交 → 可恢复结果”的职责划分。SQLBot 第一阶段采用 PostgreSQL 作为幂等与推进的唯一事实来源，不照搬 Redis processing/replay 双键、题号锁和多级 single-flight 全套结构。

面试系统防止重复评分、重复跳题；SQLBot 防止重复建记录、重复发起问数、重复提交结果、并发污染上下文。两者业务副作用不同，不能直接把 questionNumber 换成 question 文本。

## 2. AI-meeting 实际调用链

```text
InterviewSessionController.answerInterviewQuestion
  → InterviewSessionFacade.answerInterviewQuestion
    → 校验会话归属/状态，READY → IN_PROGRESS
    → InterviewAgentOrchestrationService.answerInterviewQuestion
      → InterviewAnswerPipeline.execute
        → 参数校验、requestId 归一化
        → Redis 成功回放 / processing 门禁
        → Redis 未命中时尝试运行快照回放
        → 加载当前题与流程，检查题号
        → sessionId + 当前题号互斥锁
        → 锁后再次校验题号
        → 评分计算（尚不累计分数）
          → InterviewAiInvoker
            → DistributedInterviewAiSingleFlightService
              → AiCallGuardService → AI 调用
        → 规则判断追问/推进主问题、提交分数
        → 幂等追加轮次日志；失败则进入修复队列
        → 保存成功回放，提交快照刷新
        → finally 释放题锁，清理未成功的 processing
```

### 2.1 四种保护不是同一件事

|层次|实际职责|SQLBot 对应设计|
|---|---|---|
|请求幂等|同 sessionId/requestId 的请求返回处理状态或已有结果|稳定操作键、任务唯一约束、状态查询和回放|
|题目互斥|不同 requestId 也不能同时推进同一题；锁后检查旧题号|会话活跃任务约束、上下文版本/执行代次复核|
|AI single-flight|相同阶段、会话、题号和输入摘要的 AI 调用共享结果|以后可做阶段计算合并；第一阶段不要求独立实现|
|状态/日志/快照恢复|恢复业务游标、轮次与回放，补偿日志写失败|同库结果与状态事务提交、持久化待执行任务、失联任务处置|

### 2.2 已确认的实现细节

- 请求未传 requestId 时，以 sessionId、题号和完整回答拼接生成 SHA-256 的前32位；显式 requestId 则 trim 后使用。
- 幂等服务先 GET replay，未命中则 SETNX processing。成功后先写 replay，再删除 processing；异常退出清理 processing。
- 仓库 application.yaml 配置：processing 基础120秒、长尾300秒，代码取较大值；成功回放24小时；题锁等待0毫秒；lock-expire-seconds=-1 且 watchdog=true，因此该配置走 Redisson 看门狗。类字段默认的120秒固定租约不能与配置文件生效值混淆，部署覆盖值未核验。
- Redis 幂等首次请求分支会调用 findReplayResponse，尝试从运行快照重建响应。快照查找先匹配 requestId，再按题号和回答摘要匹配；后者使用截断到1000字符的回答。
- 模型先产出结构化评分，再由代码决定追问/推进。分数提交失败时尝试恢复推进前 flow；这属于补偿，不是一个数据库原子事务。
- finishAndReturn 中，轮次日志追加失败会提交修复队列，然后写 replay，再请求刷新运行快照。快照刷新经协调器提交，不能视为与业务推进同步原子提交。
- AiCallGuardService 有按阶段的 Bulkhead、CircuitBreaker、TimeLimiter、Retry。InterviewEvaluationService 在工作流结果解析失败时可降级为 prompt 直评，保护异常会继续向上传递。
- 分布式 single-flight 包含 owner/follower、ownerToken、心跳和结果回放；HYBRID 模式遇到 RuntimeException 可以回退本地 single-flight。它不能据此承诺降级后仍具备跨节点去重。

### 2.3 借鉴时要补强的边界

下列是依据调用顺序识别的设计风险，不是已经复现的生产故障：

1. **显式同键不同内容**：请求级 Redis replay 按 sessionId/requestId 返回，未见在这一层比较完整请求指纹。SQLBot 应明确返回冲突，不能回放另一次输入的结果。
2. **固定 processing TTL 与执行者所有权**：processing 值为固定字符串1，清理不是按 owner 比较删除。题锁及 AI single-flight 提供额外保护，但不能把单独的 processing 键当作完整执行权协议。
3. **多次写入的崩溃窗口**：推进、分数、轮次、replay、快照并非同一个事务。发生响应丢失或进程退出时，需要恢复与补偿，不能仅凭有幂等服务就称为 exactly-once。
4. **失败不应无条件重新计算**：推进已经发生而后续写入失败，与尚未开始模型调用，是不同状态。SQLBot 不采用“任何异常都删除标记，让客户端从头重来”。
5. **本地降级会改变一致性保证**：Redis 不可用时回到本地 single-flight 只保护单进程。SQLBot 持久化幂等库不可用时应拒绝接单，不能改用进程字典继续推进。
6. **摘要不能代替完整请求身份**：SQLBot 请求指纹使用完整的规范化请求。摘要/截断只用于展示与上下文压缩，不能参与可能合并不同业务请求的身份判断。

## 3. SQLBot 当前接入点

`/chat/question` 当前把 ChatQuestionBase 重建为 ChatQuestion，仅传 chat_id 和 question；添加字段后必须贯穿这里。`stream_sql` 创建 LLMService，init_record 调用 save_question，然后 executor.submit；save_question 内自行 commit。

后台 run_task 分阶段保存 SQL、数据、图表、错误，使用内存 Future 和 chunk_list 向 SSE 输出。ChatRecord 的 finish/error 可记录结果，但不能表达任务领取、租约、执行代次和重复请求回放。

因此，**只在 API 顶部加缓存或只给 save_question 加唯一键，都不能保证整个链路不重复推进**。第一阶段必须划清接单事务、外部计算和阶段提交事务。

## 4. 第一阶段接口契约

### 4.1 稳定请求身份

网页优先使用请求体 `request_id`，便于兼容现有流式封装；Java 客户端可以使用同一字段或 `Idempotency-Key`。同时存在时必须相等。建议UUID，设长度上限，不把用户问题本身当键。

幂等命名空间拟定为 `(oid, requested_by, operation, idempotency_key)`，全部 NOT NULL。chat_id、问题全文、显式数据源、重新生成目标、结果阶段等影响语义的字段进入规范化请求指纹。operation 使用 QUESTION、REGENERATE 等稳定值，不使用URL作为唯一身份。

指纹采用固定版本的规范化 JSON 与 SHA-256。不要删除问题中有意义的空格或标点。服务端推导的模型/schema/权限版本存入首次执行快照；重试先复用既有快照，不因默认模型切换而误判同请求为不同请求。显式传入的模型选择仍属于请求指纹。

客户端超时重试保持 request_id；主动重新生成、刷新数据或再次提交同一句话必须生成新键。初期可兼容缺键旧客户端，但必须标记“未提供跨重试幂等保证”，在所有入口升级前不能宣称全链路完成。

### 4.2 返回语义

先验证登录、工作空间和会话访问权，再查幂等结果；回放还需验证当前数据源/结果访问权。

|情况|行为|
|---|---|
|新键|原子接单，返回稳定 task_id/chat_record_id，启动或等待执行|
|同键同内容，QUEUED/RUNNING|返回202和任务状态/查询地址；不再创建LLMService或重复提交线程池|
|同键同内容，SUCCEEDED/PARTIAL|返回已持久化结果及时间、replayed=true；不重新调用模型/SQL|
|同键不同内容|409 IDEMPOTENCY_KEY_REUSED|
|同会话另一任务活跃|409 CHAT_BUSY，附有权查看的活跃任务信息；本次未被接单|
|已失败/需要人工处理|返回原任务终态和原因；同键重试本身不重置任务|
|结果已清理但幂等墓碑仍在|410 RESULT_EXPIRED，要求新操作键，不能悄悄重执行|
|幂等数据库不可用|503，禁止降级为无幂等执行|

拟增加 `GET /chat/tasks/{task_id}` 查询状态；终态结果可由该接口返回或引用已有记录接口。路径需在实现时核对路由顺序，避免被 `/{chart_id}` 捕获。

SSE 继续用于新任务的实时输出。重复请求先按上述 HTTP 状态处理，前端必须支持202/409，不能假定所有响应都是SSE。连接断开不等于任务失败；客户端查询原任务。第一阶段不承诺逐token事件重放，只保证任务与最终结果回放；如以后增加事件重连，使用持久化序号和 Last-Event-ID，不重跑任务补流。

## 5. PostgreSQL 事实来源与事务边界

### 5.1 拟议数据模型

新增 query_task，保留现有 ChatRecord/ChatLog 作为业务输出与诊断记录。

|字段组|内容|
|---|---|
|身份|id、oid、requested_by、chat_id、operation、idempotency_key|
|请求|request_hash、hash_version、request_payload(JSONB)、context_snapshot(JSONB)|
|进度|status、stage、version、attempt、owner_id、owner_epoch|
|租约|lease_until(TIMESTAMPTZ)、heartbeat_at、deadline_at|
|关联|chat_record_id（唯一）、result_ref/结果版本、error_code、retryable|
|时间|created_at、started_at、finished_at、updated_at、retention_until|

关键约束草案（不是已执行迁移）：

```sql
UNIQUE (oid, requested_by, operation, idempotency_key)
UNIQUE (chat_record_id)

CREATE UNIQUE INDEX uq_query_task_active_chat
ON query_task (oid, chat_id)
WHERE status IN ('QUEUED', 'RUNNING', 'RECOVERY_PENDING');
```

必须为 status/stage 增加合法值约束、外键和领取索引；核心身份不可使用NULL绕过唯一性。过期判断不写成依赖 now() 的部分索引条件，而由受控状态转换移出活跃集合。实际DDL留待实现时根据迁移命名、删除策略与保留期确定。

### 5.2 接单事务

1. 权限校验后开始短事务，按约定顺序锁定会话行，并再次确认会话有效。
2. 查询该幂等命名空间是否已存在；存在则校验指纹并返回既有状态，不创建记录。
3. 若为新请求，确认该会话没有活跃任务。
4. 插入任务及初始 ChatRecord，关联ID，提交同一事务。
5. 唯一约束作为最终竞争保护。另一会话重复使用同一命名空间键时，用明确冲突处理，不覆盖旧请求。

`INSERT ... ON CONFLICT` 可承担唯一冲突处理。READ COMMITTED 下，DO NOTHING 后未返回行并不意味着没有已存在任务；用后续查询读取已提交结果，必要时有界重试。不能只做“先SELECT不存在，再INSERT”而没有唯一约束。[PostgreSQL INSERT 文档](https://www.postgresql.org/docs/18/sql-insert.html)

现有 save_question 的内部 commit 需要拆分：新增可由调用者控制事务的保存方法，保留旧调用兼容，不能让任务记录与问题记录分两次提交。

### 5.3 持久化接单与工作领取

API提交后即使进程在 submit 前退出，QUEUED 任务也必须能再次被发现。建议以 PostgreSQL 任务表兼任初期持久化队列，由受控轮询器领取；不必新增消息队列。可用 FOR UPDATE SKIP LOCKED 获取可领取任务，事务内写 owner/epoch/租约后立即提交；仅在本地有空闲并发额度时领取。[PostgreSQL SELECT 文档](https://www.postgresql.org/docs/18/sql-select.html)

普通工作进程可以多个实例竞争领取，不复用 SingleWorkerGuard 让所有问数只能在一个进程执行。清理/巡检是否单实例另行设计。

### 5.4 计算与提交分开

外部模型、SQL执行、图表生成都在短事务外运行。每个持久化阶段提交时：

1. 锁定任务行，检查 status、预期 stage/version、owner_epoch、owner_id 和租约。
2. 检查失败则丢弃本次输出，禁止更新 ChatRecord/ChatLog/会话标题等关联状态。
3. 检查通过，在同一事务保存阶段输出、更新任务 stage/version，写必要审计事件。
4. 终态提交同时保存最终业务结果与任务终态。

不能仅在“最终finish”处校验owner，中途SQL、数据、标题、错误保存也可能被过期线程覆盖。实现时应逐一收拢这些写入点；不把现有多个自行commit的方法简单包在一个名义事务外面。

阶段与状态分离：status可采用 QUEUED/RUNNING/SUCCEEDED/PARTIAL/FAILED/CANCELLED/RECOVERY_PENDING/NEEDS_REVIEW；stage采用 ACCEPTED/CONTEXT_READY/SQL_VALIDATED/DATA_READY/CHART_READY。达到用户请求的结束阶段即可成功。图表失败且数据可用可为PARTIAL，不能覆盖掉成功的数据结果。

## 6. 租约、崩溃与不确定结果

owner_epoch 每次合法接管递增，旧代次永远不能写入。续租同样校验当前owner与代次，并使用数据库时间。超时配置需依据测量结果设置；本设计不把AI-meeting的120/300秒直接当SQLBot最终参数。

第一阶段默认保守恢复：过期 RUNNING 转 RECOVERY_PENDING，阻止该会话启动下一任务；判断已持久化阶段后，确定安全才终止或恢复。未知外部执行状态不能直接当“未执行”自动从头重跑。

|故障点|预期处理|
|---|---|
|接单事务提交前退出|全部回滚；相同键可以正常接单|
|提交后、线程启动前退出|QUEUED仍存在，工作领取器可发现|
|模型执行中进程退出|租约过期，进入恢复判断；不得再建第二条问题记录|
|模型返回但阶段输出未保存|可能需要重新计算，明确不能保证模型物理调用一次|
|SQL已经执行、结果保存前退出|结果不确定；不猜测已保存，不默认自动重查；可进入NEEDS_REVIEW或明确失败|
|终态提交后响应丢失|同键返回已保存终态结果|
|旧线程超时后才返回|代次/租约检查阻止任何业务写入|
|权限被撤销|执行前及结果回放复核；不返回原缓存敏感结果|
|任务完成但结果超过保留期|保留幂等墓碑或明确公布幂等窗口，不能隐式作为新请求执行|

允许重试时使用独立、受授权的retry操作，携带预期版本，有界增加attempt并记录原因；普通同键提交只查询/回放。人工重新提问使用新键。终态不会因旧请求再次到达而重置。

PostgreSQL可以保证SQLBot自身任务与记录的提交一致性，但无法与外部模型服务或另一数据源建立一个普通本地事务。目标是“有效推进与结果提交去重”，不是无条件端到端exactly-once。

## 7. Python与现有运行方式的适配

- threading.Lock、asyncio.Lock、GIL 都不能跨进程保障业务幂等。多worker共享数据库约束，而非内存字典。
- 每个线程使用独立SQLAlchemy Session；不要把请求依赖中的Session传给后台线程。将已验证的不可变请求DTO/ID传入，线程内部按需创建短事务。[SQLAlchemy并发说明](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks)
- 保留现有同步模型/数据库调用时使用有界工作执行器，避免阻塞FastAPI事件循环。暂不为幂等改造把全链路改为async。
- Future.cancel()不能停止已经运行的线程任务。因此需要下游超时、协作取消与owner检查；停止等待不代表计算已停止。[Python Future 文档](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel)
- Python上下文管理器清理Session/连接；失败路径也必须经过执行权验证。不要在finally里无条件把任务改回可执行。
- JSONB保存结构化输入/快照，原始大结果优先引用ChatRecord或结果存储，不重复复制个人数据。
- 常规数据库事务错误可回滚重试事务段，不把整个模型＋SQL链路一起重试。数据库不可用则停止新的有效推进。

## 8. 最小模块与改造顺序

建议新增 query_task_model、query_task_repository、query_task_service、query_task_worker 四个职责明确的模块；不复制Java的包层级、所有single-flight组件或Resilience4j对象结构。

1. 完成任务DDL与唯一性/状态转换集成测试。
2. 实现接单、冲突、状态查询、终态回放，贯通网页request_id。
3. 将问题记录创建纳入接单事务，引入持久化QUEUED领取。
4. 将普通问数每个阶段的写入集中到受执行权约束的提交方法。
5. 加入过期任务巡检、断流查询、终态重放与必要审计。
6. 扩展REGENERATE、分析/预测以及普通MCP问数：各入口明确operation和指纹，复用同一服务。未接入入口要明确标注覆盖范围，不能绕过活跃任务保护写同一会话。

首个实现切片聚焦QUESTION，但在上线前必须解决其他写会话入口的并发策略；不能仅保护网页提问却让MCP/重新生成绕过。

## 9. 验收清单

使用真实PostgreSQL、至少两个进程，外部模型/SQL用可计数、可阻塞的测试替身。另做少量真实模型和只读数据源回归。以下均为待执行测试，不是已通过结果。

|场景|必须观察到|
|---|---|
|同键同内容20并发|一个task、一个ChatRecord，一次正常执行权领取；其余状态/回放|
|同键不同问题/会话/显式数据源|按命名空间返回409，不覆盖原请求|
|同会话不同键并发|最多一个活跃任务，其他CHAT_BUSY|
|不同会话并发|不被全局锁串行化|
|请求缺键|与文档兼容策略一致，不虚称幂等|
|接单提交与派发间kill进程|QUEUED能被发现，无丢单|
|终态提交与HTTP响应间断网|同键恢复原结果，无新模型调用|
|模型超时旧线程迟到|不写阶段、结果、标题或错误；记录拒绝事件|
|租约过期与续租竞争|只允许合法owner/epoch更新|
|阶段保存事务失败|输出与任务阶段共同回滚，不出现“成功但无结果”|
|SQL执行后结果落库前退出|显式不确定状态，不伪造恢复成功|
|同任务重复领取/重复完成事件|条件更新只允许一次有效提交|
|用户/工作空间隔离、权限撤销|不能凭键或task_id越权获取结果|
|数据源切换/会话删除/重新生成并发|遵守活跃任务与版本规则|
|SSE中断重连、202/409处理|查询既有任务，不自动产生新request_id|
|数据存在而图表失败|结果保持，返回明确PARTIAL|
|队列满/幂等库不可用|有界拒绝，不启动无保护任务|
|结果过期、幂等记录清理|与保留契约一致，不静默重复执行|

## 10. 源码证据入口

AI-meeting：

- [Controller](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/api/InterviewSessionController.java:99)
- [Facade归属与状态校验](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/flow/session/InterviewSessionFacade.java:119)
- [编排服务](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/flow/session/InterviewAgentOrchestrationService.java:51)
- [回答流水线](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/flow/answer/InterviewAnswerPipeline.java:56)
- [请求幂等服务](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/flow/answer/InterviewAnswerIdempotencyService.java:29)
- [题锁](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/flow/answer/InterviewQuestionLockService.java)
- [统一AI调用](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/shared/InterviewAiInvoker.java)
- [分布式single-flight](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/application/guard/singleflight/service/DistributedInterviewAiSingleFlightService.java)
- [AI超时/熔断/隔离/重试](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/application/guard/core/AiCallGuardService.java)
- [轮次修复](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/flow/answer/InterviewTurnRepairService.java)
- [运行快照与回放](D:/AI-Meeting/admin/src/main/java/com/hewei/hzyjy/xunzhi/interview/application/runtime/InterviewSessionRuntimeSnapshotService.java:176)
- [仓库锁与TTL配置](D:/AI-Meeting/admin/src/main/resources/application.yaml:226)
- [幂等单测](D:/AI-Meeting/admin/src/test/java/com/hewei/hzyjy/xunzhi/interview/application/pipeline/InterviewAnswerIdempotencyServiceTest.java:24)：阅读了NEW/PROCESSING/SUCCEEDED与清理后重试的测试；它使用模拟Redis，不能证明真实TTL/多节点崩溃行为。本次未运行。

SQLBot：

- [请求模型](D:/SQLBot-main/backend/apps/chat/models/chat_model.py:397)
- [入口转发与后台启动](D:/SQLBot-main/backend/apps/chat/api/chat.py:245)
- [save_question内部提交](D:/SQLBot-main/backend/apps/chat/curd/chat.py:809)
- [任务与流式输出](D:/SQLBot-main/backend/apps/chat/task/llm.py:1198)
- [网页流式调用](D:/SQLBot-main/frontend/src/api/chat.ts:17)
- [普通MCP入口](D:/SQLBot-main/backend/apps/mcp/mcp.py:167)

本次完成文档与静态源码研究。上述接口、任务表、索引、状态机、巡检及测试均为拟议设计，没有创建数据库表或修改运行系统。
