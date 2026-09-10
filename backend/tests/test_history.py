"""问答历史（跨会话语义检索）测试：假向量注入，不依赖模型文件。

链路：存储 → 相似度检索 → API → 聊天自动入库。真 embedding 的质量由人工验证
（本地实测：相关句余弦 0.93 / 无关句 0.25），CI 只验证机制，不下载模型。
"""

import asyncio

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

import db_helpers
from app import db as dbmod
from app import query
from app.ai import client as ai_client
from app.ai import embeddings
from app.api import chat as chat_api
from app.main import app

client = TestClient(app)


def _fake_embed(texts):
    """确定性假向量：按文本内容生成 512 维单位向量（同文本同向量）。"""
    out = []
    for t in texts:
        rng = np.random.default_rng(abs(hash(t)) % (2**32))
        v = rng.random(embeddings.DIM)
        out.append((v / np.linalg.norm(v)).tolist())
    return out


@pytest.fixture(autouse=True)
def patch_embed(monkeypatch):
    """全部测试替换 embed 为假向量（模型文件在 CI 不存在）。"""
    monkeypatch.setattr(embeddings, "embed", _fake_embed)


def _fresh_conn():
    dsn, name = db_helpers.create_temp_db()
    conn = psycopg.connect(dsn, row_factory=dict_row)
    dbmod.init_schema(conn)
    return conn, name


def test_insert_and_search_roundtrip():
    """相同向量检索命中自己，相似度 ≈ 1.0；created_at 为 ISO 字符串。"""
    conn, name = _fresh_conn()
    try:
        e = _fake_embed(["牛肉poke 六月卖了多少钱"])[0]
        query.insert_qa(conn, "牛肉poke 六月卖了多少钱", "13,440 元", e)
        conn.commit()
        hits = query.search_history(conn, e, 5)
        assert len(hits) == 1
        assert hits[0]["question"] == "牛肉poke 六月卖了多少钱"
        assert hits[0]["similarity"] == pytest.approx(1.0, abs=1e-6)
        assert isinstance(hits[0]["created_at"], str)
    finally:
        conn.close()
        db_helpers.drop_temp_db(name)


def test_search_orders_by_similarity():
    """距离排序：更接近查询的排在前面。"""
    conn, name = _fresh_conn()
    try:
        e_a = _fake_embed(["问题甲"])[0]
        e_b = _fake_embed(["问题乙"])[0]
        query.insert_qa(conn, "问题甲", "答案甲", e_a)
        query.insert_qa(conn, "问题乙", "答案乙", e_b)
        conn.commit()
        hits = query.search_history(conn, e_a, 5)
        assert hits[0]["question"] == "问题甲"
        assert hits[0]["similarity"] > hits[1]["similarity"]
    finally:
        conn.close()
        db_helpers.drop_temp_db(name)


def test_recent_history_newest_first():
    conn, name = _fresh_conn()
    try:
        for i in range(3):
            query.insert_qa(conn, f"问题{i}", f"答案{i}", _fake_embed([f"问题{i}"])[0])
        conn.commit()
        rows = query.recent_history(conn, 10)
        assert [r["question"] for r in rows] == ["问题2", "问题1", "问题0"]
    finally:
        conn.close()
        db_helpers.drop_temp_db(name)


# ---------- API 层 ----------

def test_history_api_search_and_recent():
    """通过 app 库（session 测试库）：写入 → 检索/最近接口可用；空 q 400。"""
    conn = dbmod.get_conn()
    try:
        emb = _fake_embed(["客单价最近是涨了还是跌了"])[0]
        query.insert_qa(conn, "客单价最近是涨了还是跌了", "上涨 0.71 元", emb)
        conn.commit()
    finally:
        conn.close()

    r = client.get("/api/history/search", params={"q": "客单价涨跌"})
    assert r.status_code == 200
    hits = r.json()
    assert any("客单价" in h["question"] for h in hits)

    r2 = client.get("/api/history/recent", params={"limit": 5})
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)

    assert client.get("/api/history/search", params={"q": " "}).status_code == 400


def test_history_search_503_without_model(monkeypatch):
    """模型缺失时接口明确 503（而不是 500 / 编造）。"""
    def _broken(_texts):
        raise RuntimeError("embedding 模型缺失")

    monkeypatch.setattr(embeddings, "embed", _broken)
    r = client.get("/api/history/search", params={"q": "任意"})
    assert r.status_code == 503


# ---------- 聊天自动入库 ----------

class _Delta:
    def __init__(self, content):
        self.choices = [type("C", (), {"delta": type("D", (), {
            "content": content, "reasoning_content": None, "tool_calls": None})()})()]


class _FakeCompletions:
    async def create(self, **kwargs):
        async def gen():
            yield _Delta("这是模拟回答")
        return gen()


def test_chat_saves_history(monkeypatch):
    """问答完成后自动入库（跨会话记忆的数据来源）。"""
    fake = type("F", (), {"chat": type("C", (), {"completions": _FakeCompletions()})()})()
    monkeypatch.setattr(ai_client, "get_client", lambda: fake)

    conn = dbmod.get_conn()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM qa_history").fetchone()["n"]
    finally:
        conn.close()

    async def drain():
        events = []
        async for ev in chat_api.run_chat([{"role": "user", "content": "测试问题：你好"}]):
            events.append(ev)
        return events

    events = asyncio.run(drain())
    assert any(ev["type"] == "done" for ev in events)

    conn = dbmod.get_conn()
    try:
        after = conn.execute("SELECT COUNT(*) AS n FROM qa_history").fetchone()["n"]
        last = conn.execute(
            "SELECT question, answer FROM qa_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert after == before + 1
    assert last["question"] == "测试问题：你好"
    assert last["answer"] == "这是模拟回答"


def test_chat_survives_embedding_failure(monkeypatch):
    """embedding 失败（如模型缺失）不得阻断回答（红线：问答永远可用）。"""
    fake = type("F", (), {"chat": type("C", (), {"completions": _FakeCompletions()})()})()
    monkeypatch.setattr(ai_client, "get_client", lambda: fake)

    def _broken(_texts):
        raise RuntimeError("embedding 模型缺失")

    monkeypatch.setattr(embeddings, "embed", _broken)

    async def drain():
        events = []
        async for ev in chat_api.run_chat([{"role": "user", "content": "再来一个问题"}]):
            events.append(ev)
        return events

    events = asyncio.run(drain())
    assert any(ev["type"] == "done" for ev in events)
    text = "".join(ev["data"] for ev in events if ev["type"] == "delta")
    assert "这是模拟回答" in text
