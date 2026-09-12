# FastAPI 学习路线：基于 SQLBot 项目

## 学习目标

你已经有 Java 后端经验，也学过 Python 基础语法，所以学习 FastAPI 不需要从“变量、循环、函数”重新开始。建议目标是：能读懂 SQLBot 的后端启动流程、接口组织方式、请求响应模型、依赖注入、安全中间件、数据库访问和异常处理。

学完这一阶段后，你应该能做到：

- 看懂 `backend/main.py` 是如何创建 FastAPI 应用的。
- 看懂 `backend/apps/api.py` 如何聚合各业务模块路由。
- 能顺着一个前端请求找到对应的后端接口、参数模型、业务逻辑和数据库操作。
- 能理解认证、权限、CORS、中间件、统一响应这些后端基础设施。
- 能自己新增一个简单 API，并接入已有项目结构。

## 1. FastAPI 在这个项目里的位置

SQLBot 后端主框架是 FastAPI，对应 Java 生态里大致可以类比为 Spring Boot。

重点文件：

- `backend/main.py`：应用入口，创建 FastAPI 实例，挂载中间件和路由。
- `backend/apps/api.py`：路由聚合入口。
- `backend/apps/*/api/*.py`：各业务模块接口。
- `backend/common/core/config.py`：配置项读取。
- `backend/common/core/deps.py`：通用依赖。
- `backend/common/core/db.py`：数据库会话。
- `backend/common/core/response_middleware.py`：响应处理中间件。
- `backend/apps/system/middleware/auth.py`：认证相关中间件。

先不用急着读 AI Agent 逻辑。FastAPI 阶段先把“一个 Web 请求如何进来、如何校验、如何路由、如何返回”看明白。

## 2. 第一阶段：FastAPI 基础概念

先补这些概念：

- `FastAPI()` 应用实例。
- 路由装饰器：`@router.get`、`@router.post`、`@router.put`、`@router.delete`。
- `APIRouter`：模块化路由。
- 请求参数来源：路径参数、查询参数、请求体、Header、Cookie。
- 响应模型：Pydantic / SQLModel 模型。
- 自动生成接口文档：`/docs`、`/openapi.json`。

项目阅读任务：

1. 打开 `backend/main.py`，找到 `app = FastAPI(...)`。
2. 找到项目如何设置 `openapi_url`、`docs_url`。
3. 打开 `backend/apps/api.py`，观察各业务模块如何 `include_router`。
4. 随便选一个简单模块，比如系统变量、用户、工作空间，找到它的 `@router.get` 或 `@router.post`。

你要形成的理解：

```text
FastAPI app
  -> include_router
  -> APIRouter
  -> 具体接口函数
  -> 业务 CRUD / Service
  -> 数据库 / 外部服务
```

## 3. 第二阶段：请求和响应模型

FastAPI 的一个核心是类型驱动。它大量依赖 Python 类型注解和 Pydantic 模型来完成参数校验、接口文档生成和数据序列化。

需要掌握：

- Pydantic / SQLModel 的字段定义。
- 请求体模型。
- 响应模型。
- 可选字段、默认值、列表、嵌套对象。
- 字段校验和类型转换。

项目阅读任务：

1. 看 `backend/apps/system/schemas/`。
2. 看 `backend/apps/datasource/models/` 和 `backend/apps/datasource/api/`。
3. 对照一个接口函数，找它的请求参数类型和返回值。

类比 Java：

```text
Pydantic / SQLModel 模型
  类似 DTO / VO / Entity 的混合角色

FastAPI 参数校验
  类似 Spring MVC + validation 注解
```

注意：Python 项目里 DTO、Entity、Schema 的边界通常没有 Java 项目那么重，具体要看项目约定。

## 4. 第三阶段：依赖注入 Depends

FastAPI 的依赖注入不像 Spring 那样是全局容器 Bean 管理，而是通过函数参数声明依赖。

需要掌握：

- `Depends(...)`
- 依赖函数可以返回数据库 session、当前用户、权限上下文等。
- 依赖可以嵌套。
- 依赖可以作用在单个接口、路由组或全局应用上。

项目阅读任务：

1. 看 `backend/common/core/deps.py`。
2. 搜索 `Depends`，观察它在各 API 中的使用方式。
3. 找一个需要登录的接口，理解“当前用户”是怎么传进去的。

重点理解：

```text
接口函数参数
  -> Depends 解析依赖
  -> 获取 DB session / 当前用户 / 权限信息
  -> 执行业务逻辑
```

## 5. 第四阶段：中间件和请求生命周期

中间件用于处理每个请求的公共逻辑，比如认证、日志、CORS、统一响应、异常处理等。

需要掌握：

- FastAPI / Starlette 中间件概念。
- 请求进入路由前会经过中间件。
- 响应返回前也会经过中间件。
- 中间件适合放公共横切逻辑。

项目阅读任务：

1. 看 `backend/main.py` 里注册了哪些 middleware。
2. 看 `backend/common/core/response_middleware.py`。
3. 看 `backend/apps/system/middleware/auth.py`。
4. 理解哪些路径会跳过认证，比如登录、文档、静态资源等。

类比 Java：

```text
FastAPI Middleware
  类似 Spring Interceptor / Filter
```

## 6. 第五阶段：认证和安全

SQLBot 是一个完整 Web 系统，不是单纯 demo，所以认证逻辑很值得看。

需要掌握：

- JWT 基础。
- Header 中携带 token。
- 登录接口如何签发 token。
- 后续请求如何校验 token。
- 白名单路径。
- 权限校验和资源隔离。

项目阅读任务：

1. 看 `backend/common/core/security.py`。
2. 看 `backend/common/utils/utils.py` 中 token 相关方法。
3. 看 `backend/apps/system/api/login.py`。
4. 看 `backend/apps/system/middleware/auth.py`。

你要能回答：

- 用户登录后 token 是在哪里生成的？
- 前端后续请求如何携带 token？
- 后端在哪里解析 token？
- 哪些接口不需要登录？

## 7. 第六阶段：数据库访问

FastAPI 本身不绑定数据库，SQLBot 使用 SQLModel / SQLAlchemy 访问 PostgreSQL。

需要掌握：

- Session / connection 的概念。
- ORM 模型。
- 查询、新增、更新、删除。
- 事务提交和回滚。
- Alembic 数据库迁移。

项目阅读任务：

1. 看 `backend/common/core/db.py`。
2. 看 `backend/apps/system/models/`。
3. 看 `backend/apps/system/crud/`。
4. 看 `backend/alembic/versions/`。

类比 Java：

```text
SQLModel / SQLAlchemy
  类似 JPA / MyBatis 的数据访问层

Alembic
  类似 Flyway / Liquibase
```

## 8. 第七阶段：异常处理和统一响应

项目阅读时要关注接口失败时是如何返回错误的。

需要掌握：

- `HTTPException`
- 自定义异常。
- 全局异常处理。
- 统一响应格式。
- 错误码和错误信息。

项目阅读任务：

1. 看 `backend/common/error.py`。
2. 搜索 `raise HTTPException`。
3. 搜索项目里的自定义错误返回。
4. 对照前端请求封装，看前端如何处理错误。

前端可看：

- `frontend/src/utils/request.ts`

## 9. 第八阶段：从一个接口完整走读

当你掌握前面内容后，建议做一次完整链路阅读。

推荐链路：

```text
前端登录页面
  -> frontend/src/api/login.ts
  -> backend/apps/system/api/login.py
  -> backend/common/core/security.py
  -> backend/apps/system/middleware/auth.py
```

再看数据源链路：

```text
前端数据源页面
  -> frontend/src/api/datasource.ts
  -> backend/apps/datasource/api/datasource.py
  -> backend/apps/datasource/crud/datasource.py
  -> backend/apps/datasource/models/datasource.py
```

最后再进入问答链路：

```text
前端聊天页面
  -> frontend/src/api/chat.ts
  -> backend/apps/chat/api/chat.py
  -> backend/apps/chat/task/
```

问答链路比较复杂，建议放到 FastAPI 基础之后再专门学 Agent / RAG / Text-to-SQL。

## 10. 建议练习

为了真正掌握，建议做三个小练习：

1. 新增一个只返回服务状态的接口，例如 `GET /api/v1/learning/ping`。
2. 新增一个接收 JSON 请求体的接口，例如提交一个学习笔记标题和内容。
3. 新增一个需要登录才能访问的接口，练习 `Depends` 和认证上下文。

练习重点不是功能复杂，而是熟悉项目的接口组织方式。

## 11. 今日学习建议

今天只建议完成这些：

1. 读 `backend/main.py`。
2. 读 `backend/apps/api.py`。
3. 找到一个最简单的 API 文件，理解 `APIRouter`。
4. 打开浏览器访问 SQLBot 的 `/docs` 接口文档。
5. 对照页面请求，找到后端接口函数。

不要一开始就钻进 `backend/apps/chat/task/llm.py`。那里是核心但复杂，适合等 FastAPI、数据库访问和模型调用有框架感之后再读。

