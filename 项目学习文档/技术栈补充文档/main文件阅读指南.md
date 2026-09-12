# backend/main.py 阅读指南

## 这个文件的定位

`backend/main.py` 是 SQLBot 后端服务的总入口文件。

如果用 Java 后端来类比，它类似于：

- Spring Boot 的启动类
- Web 配置类
- Swagger 配置
- Filter / Interceptor 注册处
- 全局异常处理配置
- 应用启动初始化逻辑

它不是核心业务文件。它主要负责把 SQLBot 后端应用“组装起来”，具体业务逻辑分散在 `backend/apps/` 下面。

## main.py 主要包含什么

### 1. 启动初始化逻辑

文件里定义了应用启动时要做的准备工作，包括：

- 执行数据库迁移
- 初始化缓存
- 初始化动态跨域配置
- 补齐术语库 embedding
- 补齐训练数据 embedding
- 补齐数据源、表、字段 embedding
- 清理扩展包缓存
- 处理已有模型配置中的敏感信息
- 启动扩展监控

这些逻辑集中在 `lifespan` 中。

可以理解为：SQLBot 正式接收请求前，先把数据库、缓存、AI 检索数据、模型配置等基础环境准备好。

### 2. 创建 FastAPI 主应用

`main.py` 中创建了主 FastAPI 应用：

```python
app = FastAPI(...)
```

这个 `app` 是 SQLBot 的主后端服务，负责承载普通 Web API、接口文档、中间件、异常处理和业务路由。

Docker 部署时，主服务运行在 `8000` 端口。

### 3. 自定义接口文档

SQLBot 没有直接使用 FastAPI 默认的 `/docs`，而是自定义了：

- `/docs`
- `/openapi.json`

它这样做主要是为了支持：

- 中文/英文接口文档
- 自定义 Swagger 静态资源
- 接口分组说明翻译
- OpenAPI 文档缓存

这部分属于开发辅助能力，方便开发者和外部系统查看 API。

### 4. 创建 MCP 应用

除了主应用 `app`，文件中还创建了一个 `mcp_app`。

它用于承载 MCP 相关能力，以及图片静态资源访问。

Docker 部署时，MCP 服务运行在 `8001` 端口。

可以简单理解为：

```text
app      -> 普通 Web 后端服务
mcp_app  -> MCP / 图片访问服务
```

### 5. 暴露图片静态资源

SQLBot 问数后可能会生成图表图片。

`main.py` 会把图片目录挂载为静态资源路径：

```text
/images
```

这样外部系统或 MCP 调用方就可以通过 URL 访问生成的图片。

### 6. 注册 MCP Server

`main.py` 使用 `FastApiMCP` 把部分 SQLBot 接口包装成 MCP 工具。

它不是把所有接口都暴露出去，而是只开放指定操作，例如：

- 数据源列表
- 模型列表
- 问数接口
- 助手接口
- 工作空间列表
- token 相关接口

这说明 SQLBot 不只是一个 Web 系统，也可以作为 Agent 工具服务被其他 AI 应用调用。

### 7. 注册跨域配置

文件中通过 `CORSMiddleware` 配置跨域。

这主要是为了让前端页面可以正常请求后端接口。

如果没有跨域配置，当前端和后端地址不完全一致时，浏览器可能会拦截请求。

### 8. 注册中间件

`main.py` 中注册了多个中间件。

它们的作用大致包括：

- 登录 token 校验
- 统一响应处理
- 请求上下文保存
- 审计日志上下文
- MCP 客户端 IP 透传

从架构上看，中间件是每个请求进入业务接口前后的公共处理层。

### 9. 注册业务路由

真正的业务接口不直接写在 `main.py` 中。

`main.py` 通过下面的方式把业务路由挂进来：

```python
app.include_router(api_router, prefix=settings.API_V1_STR)
```

`api_router` 来自：

```text
backend/apps/api.py
```

后续要继续读业务代码，应该顺着这个文件往下看。

### 10. 注册全局异常处理

文件中注册了统一异常处理器。

它的作用是让系统出现异常时，不直接把原始 Python 错误暴露给前端，而是统一转换成项目约定的响应格式。

这部分可以类比 Java 中的 `@ControllerAdvice`。

### 11. 接入 xpack 扩展

`main.py` 最后还会初始化 `sqlbot_xpack`。

这代表 SQLBot 支持一些扩展能力，主应用启动时会把这些扩展接入 FastAPI 应用。

初学阶段不需要深入 xpack，只要知道它是扩展模块入口即可。

### 12. 本地开发启动入口

文件末尾有：

```python
if __name__ == "__main__":
    uvicorn.run(...)
```

这表示如果直接运行 `main.py`，会使用 `uvicorn` 启动 FastAPI 服务。

开发环境中可以用它来本地启动后端。

## 阅读 main.py 时应该抓住什么

读这个文件时，不建议一开始逐行死扣。

重点抓住这几个问题：

1. 这个项目创建了几个 FastAPI 应用？
2. 主服务和 MCP 服务分别负责什么？
3. 应用启动时做了哪些初始化？
4. 业务路由是从哪里挂进来的？
5. 请求进入业务接口前会经过哪些中间件？
6. 接口文档是如何提供的？
7. 异常是如何统一处理的？
8. 真正的业务代码应该去哪里继续看？

## main.py 和其他模块的关系

可以用下面这张简图理解：

```text
backend/main.py
  -> 创建 FastAPI 主应用 app
  -> 执行 lifespan 启动初始化
  -> 注册中间件
  -> 注册 /docs 和 /openapi.json
  -> 注册全局异常处理
  -> include_router(api_router)
        -> backend/apps/api.py
            -> apps/chat
            -> apps/datasource
            -> apps/system
            -> apps/dashboard
            -> apps/settings
  -> 创建 mcp_app
        -> MCP 工具服务
        -> /images 静态图片服务
```

## 一句话总结

`backend/main.py` 是 SQLBot 后端的启动和装配中心。

它负责把数据库初始化、缓存、接口文档、中间件、业务路由、异常处理、MCP 服务和扩展模块组合起来；真正的业务实现则主要在 `backend/apps/` 目录下。

