"""DeepSeek 客户端封装（V4 Pro，思考模式默认开启——用户决策：性能优先）。

关键点：DeepSeek V4 Pro 带工具调用的多轮请求必须回传 reasoning_content
（由 api/chat.py 负责），否则 API 返回 400。
"""

import os
from pathlib import Path

from openai import AsyncOpenAI

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

_client: AsyncOpenAI | None = None


def _load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    # 本地开发兜底：读 backend/.env（极简解析，不引入 python-dotenv）
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = _load_key()
        if not key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY：请在 backend/.env 配置（见 .env.example）")
        _client = AsyncOpenAI(api_key=key, base_url=BASE_URL, timeout=60.0)
    return _client
