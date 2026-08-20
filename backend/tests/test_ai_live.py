"""live 对账测试：真实调用 DeepSeek V4 Pro（无 key 自动 skip）。

用真实数据（根 data/ 三张 CSV 清洗入库到临时库）跑三样例问题 + 追问 + 兜底，
核心断言（题目硬指标）：「回答中每个数字 ⊆ 工具结果数字集 ∪ 其可验证运算结果（差额/和/百分比，
必须能由两个真实结果数字回推）」+ 锚点数字与库内对账一致。
思考模式开启，全文件共 6 次真实 API 调用，耗时约 1~3 分钟。
"""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from app import db as dbmod
from app import pipeline
from app import query
from app.ai import client as ai_client
from app.ai import tools as ai_tools
from app.api import chat as chat_api

_KEY = ai_client._load_key()
pytestmark = pytest.mark.skipif(not _KEY, reason="未配置 DEEPSEEK_API_KEY（backend/.env），live 测试跳过")

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module", autouse=True)
def real_db():
    """真实数据临时库；结束恢复原 APP_DB_PATH，不影响其他测试文件的夹具库。"""
    tmp = Path(tempfile.mkdtemp(prefix="moneki_live_"))
    db_path = tmp / "real.db"
    pipeline.run(data_dir=_DATA_DIR, db_path=db_path)
    old = os.environ.get("APP_DB_PATH")
    os.environ["APP_DB_PATH"] = str(db_path)
    yield db_path
    os.environ["APP_DB_PATH"] = old


def _nums(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _record(monkeypatch):
    """工具真实执行，同时记录 (name, args, result) 用于对账。"""
    orig = ai_tools.run_tool
    calls = []

    def rec(name, args, conn):
        r = orig(name, args, conn)
        calls.append({"name": name, "args": args, "result": r})
        return r

    monkeypatch.setattr(ai_tools, "run_tool", rec)
    return calls


def _result_nums(calls) -> set[float]:
    return _nums(json.dumps([c["result"] for c in calls], ensure_ascii=False))


def _derivable(base: set[float]) -> set[float]:
    """扩展：两个真实结果数字的差/和/百分比，同样视为「来自真实查询」（可回推验证）。"""
    out = set(base)
    vals = list(base)
    for i, a in enumerate(vals):
        for b in vals[i + 1:]:
            out.add(round(a + b, 2))
            out.add(round(a - b, 2))
            out.add(round(b - a, 2))
            if b:
                out.add(round(a / b * 100, 1))
                out.add(round(a / b * 100, 2))
            if a:
                out.add(round(b / a * 100, 1))
                out.add(round(b / a * 100, 2))
    return out


async def _ask(question, history=None):
    events = []
    async for ev in chat_api.run_chat((history or []) + [{"role": "user", "content": question}]):
        events.append(ev)
    text = "".join(ev["data"] for ev in events if ev["type"] == "delta")
    done = next((ev["data"] for ev in events if ev["type"] == "done"), None)
    return text, done


# ---------- 三样例问题 ----------

def test_live_store_category_top(monkeypatch):
    """「哪个品类的门店营业额最高？」→ 品类名与营业额必须与库内 Top1 一致。"""
    calls = _record(monkeypatch)
    text, _ = asyncio.run(_ask("在全部数据范围内，哪个品类的门店营业额最高？"))

    conn = dbmod.get_conn()
    bounds = query.data_bounds(conn)
    top = query.category_comparison(conn, bounds["date_min"], bounds["date_max"], "store_category")[0]

    assert top["category"] in text
    answer_nums = _nums(text)
    assert round(top["revenue"], 2) in answer_nums
    assert answer_nums <= _derivable(_result_nums(calls))


def test_live_product_june(monkeypatch):
    """「牛肉poke 六月卖了多少钱？」→ 必须等于库内六月数字 13,440.0，且工具查的是六月。"""
    calls = _record(monkeypatch)
    text, _ = asyncio.run(_ask("牛肉poke 六月卖了多少钱？"))

    answer_nums = _nums(text)
    assert 13440.0 in answer_nums, f"回答缺六月锚点数字，实际回答: {text}"
    assert answer_nums <= _derivable(_result_nums(calls))
    assert any(str(c["args"].get("start_date", "")).startswith("2026-06") for c in calls), (
        f"工具没有按六月查询: {[c['args'] for c in calls]}")


def test_live_aov_direction(monkeypatch):
    """「客单价最近是涨了还是跌了？」→ 方向词必须与库内 7月 vs 6月 客单价一致。"""
    calls = _record(monkeypatch)
    text, _ = asyncio.run(_ask("客单价最近是涨了还是跌了？"))

    assert _nums(text) <= _derivable(_result_nums(calls))
    conn = dbmod.get_conn()
    jul = query.summary(conn, "2026-07-01", "2026-07-31")["aov"]
    jun = query.summary(conn, "2026-06-01", "2026-06-30")["aov"]
    if jul == jun:
        pytest.skip("两月客单价持平，方向断言无意义")
    expected = "涨" if jul > jun else "跌"
    assert expected in text, f"方向应为「{expected}」（7月 {jul} vs 6月 {jun}），实际回答: {text}"


# ---------- 追问 + 兜底 ----------

def test_live_followup_may(monkeypatch):
    """「牛肉poke 六月卖了多少钱？」→「那五月呢？」：追问必须改用五月口径并给五月数字。"""
    calls1 = _record(monkeypatch)
    text1, done = asyncio.run(_ask("牛肉poke 六月卖了多少钱？"))
    assert 13440.0 in _nums(text1)
    assert any(str(c["args"].get("start_date", "")).startswith("2026-06") for c in calls1)

    calls2 = _record(monkeypatch)
    text2, _ = asyncio.run(_ask("那五月呢？", history=done["messages"]))

    conn = dbmod.get_conn()
    prod = query.resolve_products(conn, "牛肉poke")[0]
    may = query.metrics_by(conn, "2026-05-01", "2026-05-31", "product",
                           product_ids=[prod["product_id"]])
    may_rev = may["rows"][0]["revenue"] if may["rows"] else 0.0

    assert any(str(c["args"].get("start_date", "")).startswith("2026-05") for c in calls2), (
        f"追问没有按五月查询: {[c['args'] for c in calls2]}")
    assert may_rev in _nums(text2), f"回答缺五月数字 {may_rev}，实际回答: {text2}"
    # 追问回答可引用对话历史中的真实查询结果（如上一轮的 6 月数字），
    # 因此对账集合 = 两轮工具结果并集
    assert _nums(text2) <= _derivable(_result_nums(calls1 + calls2))


def test_live_fallback_no_fabrication(monkeypatch):
    """「北京烤鸭六月卖了多少钱？」→ 必须如实说没有，且不能冒出工具结果之外的数字。"""
    calls = _record(monkeypatch)
    text, _ = asyncio.run(_ask("北京烤鸭六月卖了多少钱？"))

    assert "没有" in text, f"兜底失败，实际回答: {text}"
    assert _nums(text) <= _derivable(_result_nums(calls))
