"""AI 一致性测试（mock LLM）：核心断言「回答中出现的每个数字 ∈ 工具结果数字集」。

mock 策略：脚本化的假 DeepSeek 按序返回 tool_calls 与流式文本；
工具真实执行（真查测试库）。假模型的最终回答文本里写死"正确答案数字"，
若管线把工具参数传错（如日期/商品错），工具结果里就不含这些数字 → 断言失败。
这从机制上证明：只要数字对得上，它一定来自工具（数据库）结果。

说明：mock 只能证明"管线把工具数字正确送达回答"，"LLM 真能生成正确参数"
由 test_ai_live.py（真 key）证明。
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import ai
from app import db as dbmod
from app.ai import client as ai_client
from app.ai import tools as ai_tools
from app.api import chat as chat_api
from app.main import app

client = TestClient(app)


# ---------- 假 DeepSeek ----------

class FakeTC:
    def __init__(self, tid, name, arguments):
        self.id = tid
        self.function = type("F", (), {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)})()


class FakeMessage:
    def __init__(self, content="", tool_calls=None, reasoning_content="这是思考过程"):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning_content


class FakeResp:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]


class FakeCompletions:
    """script：[(callable(msgs) -> FakeMessage), ...]，流式步骤返回异步生成器。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        if kwargs.get("stream"):
            async def _gen():
                async for chunk in step(kwargs["messages"]):
                    yield chunk
            return _gen()
        return FakeResp(step(kwargs["messages"]))


def _install_fake(monkeypatch, script):
    fake = type("FakeClient", (), {"chat": type("Chat", (), {"completions": FakeCompletions(script)})()})()
    monkeypatch.setattr(ai_client, "get_client", lambda: fake)
    return fake.chat.completions


def _record_tools(monkeypatch):
    """工具真实执行，但记录 (name, args, result)。"""
    orig = ai_tools.run_tool
    recorded = []

    def rec(name, args, conn):
        r = orig(name, args, conn)
        recorded.append({"name": name, "args": args, "result": r})
        return r

    monkeypatch.setattr(ai_tools, "run_tool", rec)
    return recorded


async def _run(question, history=None):
    events = []
    async for ev in chat_api.run_chat((history or []) + [{"role": "user", "content": question}]):
        events.append(ev)
    text = "".join(ev["data"] for ev in events if ev["type"] == "delta")
    done = next((ev["data"] for ev in events if ev["type"] == "done"), None)
    return text, done


def _nums(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _result_nums(recorded) -> set[float]:
    return _nums(json.dumps([r["result"] for r in recorded], ensure_ascii=False))


# ---------- 三样例问题（mock：参数正确性 + 数字一致不变量） ----------

def _answer(text):
    """最终回答：一轮工具后无 tool_calls 的普通消息（与 run_chat 真实路径一致）。"""
    def step(_msgs):
        return FakeMessage(content=text)
    return step


def test_question_store_category_top(monkeypatch):
    """「哪个品类的门店营业额最高？」→ query_metrics(store_category, 全量)。"""
    def round1(msgs):
        assert msgs[0]["role"] == "system" and "铁律" in msgs[0]["content"]
        assert msgs[-1]["content"].startswith("在全部数据范围内")
        return FakeMessage(tool_calls=[FakeTC("c1", "query_metrics", {
            "start_date": "2026-07-01", "end_date": "2026-07-08", "granularity": "store_category"})])

    comp = _install_fake(monkeypatch, [
        round1,
        _answer("门店品类营业额最高的是轻食，营业额 1079.0 元。"),
    ])
    recorded = _record_tools(monkeypatch)

    text, done = __import__("asyncio").run(_run("在全部数据范围内，哪个品类的门店营业额最高？"))

    # 工具收到正确参数
    assert recorded[0]["args"]["granularity"] == "store_category"
    assert recorded[0]["args"]["start_date"] == "2026-07-01"
    # 回答中的每个数字都来自工具结果（1079.0 = 夹具 S02 营业额）
    answer_nums = _nums(text)
    assert 1079.0 in answer_nums
    assert answer_nums <= _result_nums(recorded)
    # 历史正确回传（含 reasoning_content 与 tool_calls）
    hist = done["messages"]
    assert hist[-1]["role"] == "assistant"
    assert any(m["role"] == "assistant" and "reasoning_content" in m and "tool_calls" in m for m in hist)
    assert any(m["role"] == "tool" for m in hist)


def test_question_product_june_revenue(monkeypatch):
    """「牛肉poke 七月卖了多少钱？」→ query_metrics(product, product_name=牛肉poke)。"""
    def round1(msgs):
        return FakeMessage(tool_calls=[FakeTC("c1", "query_metrics", {
            "start_date": "2026-07-01", "end_date": "2026-07-31",
            "granularity": "product", "product_name": "牛肉poke"})])

    _install_fake(monkeypatch, [
        round1,
        _answer("牛肉poke 七月卖了 84.0 元。"),
    ])
    recorded = _record_tools(monkeypatch)

    text, done = __import__("asyncio").run(_run("牛肉poke 七月卖了多少钱？"))

    assert recorded[0]["args"]["product_name"] == "牛肉poke"
    assert recorded[0]["args"]["start_date"] == "2026-07-01"
    answer_nums = _nums(text)
    assert 84.0 in answer_nums              # 夹具 P02 七月营业额
    assert answer_nums <= _result_nums(recorded)


def test_question_aov_trend(monkeypatch):
    """「客单价最近是涨了还是跌了？」→ 数字同样必须来自工具结果。"""
    def round1(msgs):
        return FakeMessage(tool_calls=[FakeTC("c1", "query_metrics", {
            "start_date": "2026-07-01", "end_date": "2026-07-08", "granularity": "day"})])

    _install_fake(monkeypatch, [
        round1,
        _answer("最近客单价为 169.29 元，比上一周期高。"),
    ])
    recorded = _record_tools(monkeypatch)

    text, done = __import__("asyncio").run(_run("客单价最近是涨了还是跌了？"))

    answer_nums = _nums(text)
    assert 169.29 in answer_nums            # 夹具全量客单价
    assert answer_nums <= _result_nums(recorded)


def test_fallback_no_fabrication(monkeypatch):
    """「北京烤鸭六月卖了多少钱？」→ 不调工具直接如实说没有，零数字编造。"""
    def round1(msgs):
        return FakeMessage(content="数据里没有北京烤鸭这个商品，也没有烤鸭相关品类。可换个问法，比如「三文鱼poke 六月卖了多少钱？」")

    _install_fake(monkeypatch, [round1])
    recorded = _record_tools(monkeypatch)

    text, done = __import__("asyncio").run(_run("北京烤鸭六月卖了多少钱？"))

    assert recorded == []                    # 没查到数据就不调工具，绝不编
    assert "没有" in text
    assert _nums(text) == set()              # 回答里没有数字


def test_tool_no_match_reports_honestly(monkeypatch):
    """工具无匹配 → 返回结构化错误 → 假模型如实说明。"""
    def round1(msgs):
        return FakeMessage(tool_calls=[FakeTC("c1", "query_metrics", {
            "start_date": "2026-07-01", "end_date": "2026-07-08",
            "granularity": "product", "product_name": "北京烤鸭"})])

    _install_fake(monkeypatch, [
        round1,
        _answer("没有匹配「北京烤鸭」的商品，数据里没有它的销售记录。"),
    ])
    recorded = _record_tools(monkeypatch)

    text, _ = __import__("asyncio").run(_run("北京烤鸭七月卖了多少钱？"))

    assert recorded[0]["result"]["ok"] is False
    assert "没有" in text


def test_followup_context_history(monkeypatch):
    """追问「那五月呢」：历史（含工具结果）必须完整回传，模型据此改月份。"""
    received = {}

    def round1(msgs):
        received["msgs"] = list(msgs)  # 快照：run_chat 之后会继续往同一列表追加
        return FakeMessage(tool_calls=[FakeTC("c2", "query_metrics", {
            "start_date": "2026-05-01", "end_date": "2026-05-31",
            "granularity": "product", "product_name": "牛肉poke"})])

    _install_fake(monkeypatch, [
        round1,
        _answer("牛肉poke 五月卖了 0.0 元（五月无数据）。"),
    ])
    recorded = _record_tools(monkeypatch)

    history = [
        {"role": "user", "content": "牛肉poke 七月卖了多少钱？"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "query_metrics", "arguments": json.dumps({
                "start_date": "2026-07-01", "end_date": "2026-07-31",
                "granularity": "product", "product_name": "牛肉poke"})}}],
         "reasoning_content": "用户问七月牛肉poke，我需要查库"},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({
            "ok": True, "data": {"total": {"revenue": 84.0, "orders": 2, "aov": 42.0}}, "meta": {}})},
        {"role": "assistant", "content": "牛肉poke 七月卖了 84.0 元。"},
    ]

    text, _ = __import__("asyncio").run(_run("那五月呢？", history=history))

    # 历史完整回传：包含上一轮 tool 结果与 reasoning_content
    passed = received["msgs"]
    assert passed[-1]["role"] == "user" and passed[-1]["content"] == "那五月呢？"
    assert any(m["role"] == "tool" and "84.0" in m["content"] for m in passed)
    assert any(m["role"] == "assistant" and m.get("reasoning_content") for m in passed)
    # 模型改查五月（mock 层面证明管线支持；真实理解由 live 测试证明）
    assert recorded[0]["args"]["start_date"] == "2026-05-01"


# ---------- HTTP 层校验 ----------

def test_chat_http_validation():
    assert client.post("/api/chat/stream", json={"messages": []}).status_code == 400
    assert client.post("/api/chat/stream", json={"messages": [
        {"role": "system", "content": "x"}]}).status_code == 400
    assert client.post("/api/chat/stream", json={"messages": [
        {"role": "assistant", "content": "x"}]}).status_code == 400


def test_chat_http_happy_path_sse(monkeypatch):
    def round1(msgs):
        return FakeMessage(content="你好，我是经营数据分析助手。")

    _install_fake(monkeypatch, [round1])

    with client.stream("POST", "/api/chat/stream", json={"messages": [
            {"role": "user", "content": "你好"}]}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert 'data: {"type": "delta"' in body
    assert '"type": "done"' in body
