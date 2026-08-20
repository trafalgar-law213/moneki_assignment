"""访问控制测试（决策 D12）：登录/登出/会话 cookie、401 拦截、聊天接口双层限流。

用 monkeypatch 设置环境变量（security.py 惰性读 env），与其余测试互不影响：
未设置 ACCESS_PASSWORD 时保护整体关闭，既有 56 个测试照常运行。
"""

import pytest
from fastapi.testclient import TestClient

from app import security
from app.api import chat as chat_api
from app.main import app


@pytest.fixture(autouse=True)
def _protection_on(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "test-pass")
    security.limiter.reset()
    yield
    security.limiter.reset()


@pytest.fixture()
def fake_chat(monkeypatch):
    """替换真模型对话：限流测试只关心 429 拦截，不触发 DeepSeek 调用。"""

    async def fake_run_chat(messages):
        yield {"type": "done", "data": {"messages": messages}}

    monkeypatch.setattr(chat_api, "run_chat", fake_run_chat)


def _login_client():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"password": "test-pass"})
    assert r.json() == {"ok": True}
    return c


def test_login_ok_sets_cookie():
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"password": "test-pass"})
    assert r.json() == {"ok": True}
    assert security.COOKIE_NAME in r.cookies


def test_login_wrong_password_rejected():
    r = TestClient(app).post("/api/auth/login", json={"password": "wrong"})
    assert r.json()["ok"] is False


def test_token_roundtrip_and_forgery(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_SECRET", "test-secret")
    tok = security.make_token()
    assert security.verify_token(tok)
    assert not security.verify_token(tok + "x")
    assert not security.verify_token(None)
    assert not security.verify_token("")

    # 密钥不同 → 签名不匹配 → 伪造 token 被拒
    monkeypatch.setenv("ACCESS_TOKEN_SECRET", "other-secret")
    assert not security.verify_token(tok)


def test_api_blocked_without_cookie_health_and_auth_open():
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/check").json() == {"ok": False}
    assert c.get("/api/dashboard/summary").status_code == 401
    assert c.get("/api/meta").status_code == 401


def test_api_allowed_with_cookie():
    c = _login_client()
    assert c.get("/api/auth/check").json() == {"ok": True}
    assert c.get("/api/dashboard/summary").status_code == 200


def test_logout_invalidates():
    c = _login_client()
    assert c.post("/api/auth/logout").json() == {"ok": True}
    # 不带 cookie 的新客户端（= 登出后的浏览器）失去访问
    assert TestClient(app).get("/api/dashboard/summary").status_code == 401


def test_chat_rate_limit_per_ip(monkeypatch, fake_chat):
    monkeypatch.setenv("RATE_PER_MIN", "3")
    monkeypatch.setenv("RATE_GLOBAL_PER_DAY", "100")
    c = _login_client()
    body = {"messages": [{"role": "user", "content": "hi"}]}
    codes = [c.post("/api/chat/stream", json=body).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_chat_rate_limit_global(monkeypatch, fake_chat):
    monkeypatch.setenv("RATE_PER_MIN", "100")
    monkeypatch.setenv("RATE_GLOBAL_PER_DAY", "2")
    c = _login_client()
    body = {"messages": [{"role": "user", "content": "hi"}]}
    codes = [c.post("/api/chat/stream", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_protection_off_without_password(monkeypatch):
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    c = TestClient(app)
    assert c.get("/api/dashboard/summary").status_code == 200
    assert c.get("/api/auth/check").json() == {"ok": True}
