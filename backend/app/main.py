"""POKE 经营看板 + AI 数据问答 — 后端入口。

生产模式下同时托管前端构建产物（单容器部署，README 有取舍说明）。
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db as dbmod
from . import pipeline
from .api import dashboard

# chat 路由在 Step 4 接入（解耦：dashboard 不依赖 chat）
try:
    from .api import chat  # noqa: F401

    _HAS_CHAT = True
except ImportError:
    _HAS_CHAT = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动时若数据库缺失则自动跑清洗管线（幂等，毫秒级）
    if not dbmod.get_db_path().exists():
        pipeline.run()
    yield


app = FastAPI(title="POKE 经营看板", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

app.include_router(dashboard.router)
if _HAS_CHAT:
    app.include_router(chat.router)

# 前端构建产物托管（frontend/dist 存在时才挂载）
DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="static")
