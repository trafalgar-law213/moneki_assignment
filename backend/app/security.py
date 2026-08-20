"""公网访问保护：通行口令（HMAC 签名 cookie 会话）+ 聊天接口双层限流（决策 D12）。

设计要点：
- 口令来自环境变量 ACCESS_PASSWORD；未设置 = 保护整体关闭（本地开发与既有测试不受影响）。
- 会话：登录成功签发 HMAC 签名 token（有效期 24h），无状态、服务重启不失效、防伪造。
- 限流：单 IP 每分钟 RATE_PER_MIN 次 + 全站每天 RATE_GLOBAL_PER_DAY 次，只作用于 AI 聊天接口
  （看板接口轻量查询不限额）；内存计数（单容器单进程足够），重启清零可接受。
- 环境变量惰性读取（每次请求现读），测试可用 monkeypatch 精确控制。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse

COOKIE_NAME = "pokeone_access"
TOKEN_TTL_SECONDS = 24 * 3600

# 无需口令即可访问的接口（探活 + auth 三件套本身）
PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/check",
}


def protection_enabled() -> bool:
    return bool(os.environ.get("ACCESS_PASSWORD", ""))


def _secret() -> str:
    return os.environ.get("ACCESS_TOKEN_SECRET", "") or os.environ.get("ACCESS_PASSWORD", "") or "pokeone-dev"


def make_token() -> str:
    """签发会话 token：payload=过期时间戳，尾随 HMAC-SHA256 签名，base64url 编码。"""
    payload = str(int(time.time()) + TOKEN_TTL_SECONDS).encode()
    sig = hmac.new(_secret().encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + sig).decode().rstrip("=")


def verify_token(token: str | None) -> bool:
    """校验 token：签名正确且未过期。伪造/过期/缺失一律 False。"""
    if not token or not protection_enabled():
        return False
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, sig = raw.split(b".", 1)
        expect = hmac.new(_secret().encode(), payload, hashlib.sha256).digest()
        return hmac.compare_digest(sig, expect) and int(payload) > int(time.time())
    except (ValueError, IndexError):
        return False


def check_password(candidate: str | None) -> bool:
    """常数时间比较口令（防时序侧信道）。未配置口令时一律拒绝。"""
    password = os.environ.get("ACCESS_PASSWORD", "")
    return bool(password) and hmac.compare_digest(candidate or "", password)


class _RateLimiter:
    """内存双层限流：per-IP 60 秒滑窗 + 全站每日计数。"""

    def __init__(self) -> None:
        self._per_ip: dict[str, list[float]] = {}
        self._per_day: dict[str, int] = {}

    def check(self, ip: str) -> tuple[bool, str]:
        per_min = int(os.environ.get("RATE_PER_MIN", "5"))
        per_day = int(os.environ.get("RATE_GLOBAL_PER_DAY", "200"))
        now = time.time()
        today = time.strftime("%Y-%m-%d")

        window = [t for t in self._per_ip.get(ip, []) if now - t < 60]
        if len(window) >= per_min:
            self._per_ip[ip] = window
            return False, f"AI 对话太频繁：单 IP 每分钟最多 {per_min} 次，请稍后再试"
        if self._per_day.get(today, 0) >= per_day:
            return False, f"AI 对话太频繁：全站今天已达 {per_day} 次上限，明天再来吧"

        window.append(now)
        self._per_ip[ip] = window
        self._per_day[today] = self._per_day.get(today, 0) + 1
        return True, ""

    def reset(self) -> None:
        self._per_ip.clear()
        self._per_day.clear()


limiter = _RateLimiter()


async def access_control(request: Request, call_next):
    """HTTP 中间件：口令保护 + 聊天接口限流。未配置口令时完全透明放行。"""
    if not protection_enabled():
        return await call_next(request)

    path = request.url.path
    if not path.startswith("/api") or path in PUBLIC_API_PATHS:
        return await call_next(request)

    if not verify_token(request.cookies.get(COOKIE_NAME)):
        return JSONResponse({"detail": "需要访问口令", "code": "auth_required"}, status_code=401)

    if path == "/api/chat/stream":
        ok, msg = limiter.check(request.client.host if request.client else "unknown")
        if not ok:
            return JSONResponse({"detail": msg, "code": "rate_limited"}, status_code=429)

    return await call_next(request)
