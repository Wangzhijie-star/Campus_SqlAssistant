# Ubuntu 前后端启动与联调

适用环境：Windows + WSL Ubuntu，已有项目 `/home/sqlbotdev/sqlbot-local`。

已核对该目录中的后端 `.venv`、前端 `node_modules`、Node.js 和 npm 均存在。以下使用现有依赖和配置，不需要重新安装。

## 1. 打开 Ubuntu

在 Windows Terminal 中打开两个 Ubuntu 标签页，分别运行后端和前端。也可以在两个 PowerShell 窗口中分别执行：

```powershell
wsl -d Ubuntu -u sqlbotdev
```

下面的命令均在 Ubuntu 中执行。服务以前台方式运行，启动后保留终端窗口。

## 2. 检查 PostgreSQL

```bash
pg_lsclusters
```

本机集群为 `18 main`，端口为 `5432`。状态为 `online` 时直接继续；若为 `down`，执行：

```bash
sudo pg_ctlcluster 18 main start
pg_lsclusters
```

2026-09-13 已接入专用 Redis 连接和执行期间的双锁门禁。启动后端前执行：

```bash
sudo service redis-server start
redis-cli -h 127.0.0.1 -p 6379 ping
```

预期返回 `PONG`。已配置但无法连接 Redis 时，后端会拒绝启动；未配置此项的环境跳过连接初始化，但受双锁保护的问数操作会返回 503。原有缓存配置保持原样。

当前 Ubuntu `.env` 使用：

```dotenv
IDEMPOTENCY_REDIS_URL=redis://127.0.0.1:6379/1
IDEMPOTENCY_REDIS_KEY_PREFIX=sqlbot:local
```

可以使用应用配置和客户端检查真实连接（只发送 PING，不创建锁）：

```bash
cd /home/sqlbotdev/sqlbot-local/backend
source .venv/bin/activate
python ../tests/check_idempotency_redis_connection.py
```

2026-09-13 已在 Ubuntu 副本通过 4 项连接基础测试和上述真实连接检查。客户端保存在 `app.state.idempotency_redis`，两个键名函数位于 `app.state.idempotency_keys`；该异步客户端只应在应用事件循环使用。客户端的连接与命令超时均为 3 秒，应用退出时关闭。键名分别为 `sqlbot:local:request:processing:{chat_id}:{request_id}` 和 `sqlbot:local:chat:lock:{chat_id}`。双锁获取、续期和安全释放已实现，成功回放尚未实现。

## 3. 终端一：启动后端

逐行执行，上一条成功后再执行下一条：

```bash
cd /home/sqlbotdev/sqlbot-local/backend
source .venv/bin/activate
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

需要在 `backend` 目录运行，因为配置从相对路径 `../.env` 读取。沿用 Ubuntu 项目原来的数据库、模型和文件目录配置。

看到 `Application startup complete`，且没有后续启动异常，表示后端完成启动。本地 embedding 模型首次加载可能需要一些时间。

后端地址：`http://localhost:8000`。问数接口：`http://localhost:8000/api/v1/chat/question`。

此命令没有开启自动重载。修改后端代码后，在该终端按 `Ctrl+C`，等进程退出，再执行最后一条启动命令。

## 4. 终端二：启动前端

```bash
cd /home/sqlbotdev/sqlbot-local/frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

开发命令会先进行 TypeScript 类型检查，再启动 Vite。等待终端显示访问地址。

在 Windows 浏览器打开：

```text
http://localhost:5173
```

联调统一使用 `localhost` 访问页面，便于与后端允许的前端来源保持一致。`--strictPort` 会在 5173 被占用时直接报错，避免自动换端口后产生跨域问题。

现有 `frontend/.env.development` 的接口地址为：

```dotenv
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

如果修改此环境文件，需要重新启动前端。

## 5. 确认两个服务都能访问

可另开一个 Ubuntu 终端查看监听状态：

```bash
ss -ltnp 'sport = :8000 or sport = :5173'
```

浏览器打开页面，登录后按 `F12`，进入 Network，发送一个问题。检查 `/chat/question` 的地址是否指向 `http://localhost:8000/api/v1/chat/question`。

常见情况：

| 现象 | 处理方式 |
| --- | --- |
| 8000 或 5173 端口已占用 | 用上面的 `ss` 命令确认进程；已有服务可直接使用，需要重启时在原终端按 `Ctrl+C` |
| 前端尚未显示访问地址 | 等待类型检查；若检查报错，先处理终端报错 |
| 接口连接被拒绝 | 检查后端终端是否启动完成，是否已退出 |
| 出现 CORS 错误 | 确认页面使用 `http://localhost:5173`，并检查项目根目录 `.env` 中的 `FRONTEND_HOST` 或 `BACKEND_CORS_ORIGINS` 是否允许此来源；修改后重启后端 |
| 页面能打开，但问数失败 | 查看后端终端和 Network 响应，区分模型、数据源、认证和参数校验错误 |

## 6. 本次 request_id 联调前的版本确认

**2026-09-12：request_id 的 9 个功能文件及 2 个测试文件已合并到 Ubuntu 运行副本。已有后端进程需要重启以加载修改，浏览器也需要刷新后再发送新问题。** 上面的命令启动的是 Ubuntu 副本；以后在 D 盘修改代码，仍需同步才能生效。

本次原文件备份位于 `/home/sqlbotdev/sqlbot-local/.request-id-backups/prepared-20260912-181929`。合并清单和差异保存在 Windows 项目的 `tools/request_id_sync/prepared-20260912-181929` 中；配置、数据库和术语文件未修改。

合并后已在 Ubuntu 副本通过 6 项后端请求测试、3 项前端 ID 测试和前端 TypeScript 类型检查。前端测试使用现有 esbuild 编译后执行，因为本机 Node 未编译原生 TypeScript 支持。尚未重启服务或完成浏览器真实问数联调。

两个目录的对应关系：

| 用途 | 路径 |
| --- | --- |
| Windows 开发源码 | `D:\SQLBot-main` |
| Ubuntu 中访问上述源码 | `/mnt/d/SQLBot-main` |
| Ubuntu 实际启动的源码 | `/home/sqlbotdev/sqlbot-local` |

本次已合并的源码文件如下：

```text
backend/apps/chat/models/question_request.py（新增）
backend/apps/chat/models/chat_model.py
backend/apps/chat/api/chat.py
backend/apps/chat/task/llm.py
backend/apps/mcp/mcp.py
frontend/src/utils/questionRequest.ts（新增）
frontend/src/api/chat.ts
frontend/src/views/chat/index.vue
frontend/src/views/chat/answer/ChartAnswer.vue
```

Ubuntu 副本包含前期术语实验修改，不能直接用整个 Windows 目录覆盖。同步时保留 Ubuntu 已有 `.env`、`.venv`、`node_modules` 和前期业务修改。本次传输字段改动不需要数据库迁移。

同步并重启后，检查以下结果：

1. 普通提问请求体包含非空 `request_id`。
2. SSE 的 `type: "id"` 事件包含同一个 `request_id`；事件中的 `id` 是后端问答记录 ID。
3. 主动再次提问或点击“重新生成”使用新 `request_id`。
4. 缺失、空字符串或纯空白 `request_id` 的请求返回 HTTP 422。

当前已增加请求占位和会话锁，执行中的同一请求重发返回 PROCESSING，不同请求竞争同一会话返回 CHAT_BUSY。成功回放尚未实现：原任务完成并释放锁后，相同 request_id 重发仍可能再次执行。详细行为见同目录的《问数双锁第一阶段实现与验证》。

## 7. 停止服务

在前端终端和后端终端分别按 `Ctrl+C`，等待退出即可。无需为了停止前后端而关闭 PostgreSQL。
