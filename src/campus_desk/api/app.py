"""FastAPI 应用工厂（M6）：create_app 延迟构建，依赖注入承载在 app.state。

设计约束：
- 不建模块级 app 实例——uvicorn 用 `--factory` 延迟创建（import 即建会
  开 SqliteSaver 写 checkpointer.db，CI/测试 import 有副作用）
- 测试/冒烟一律 `create_app(session_factory=..., registry=...)` 注入；
  无参调用会建真 LLM + 默认 MySQL 工厂（生产路径，勿在测试出现）
- 同步路由（def）跑线程池：turn/DB 都是同步代码，async def 会阻塞事件循环
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from campus_desk.api.graphs import GraphRegistry
from campus_desk.api.routes import auth, chat, tickets
from campus_desk.db.session import default_session_factory


def create_app(*, session_factory=None, registry: GraphRegistry | None = None) -> FastAPI:
    app = FastAPI(title="CampusDesk API", version="0.1.0")
    # Vite dev server（5173）跨域；生产同源（nginx 反代）不需要
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.session_factory = session_factory or default_session_factory()
    app.state.registry = registry or GraphRegistry(app.state.session_factory)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(tickets.router)
    return app
