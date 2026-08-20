"""AI 数据问答（SSE 流式）。

链路：自然语言 → DeepSeek V4 Pro（思考模式开）出工具调用 → 后端白名单 SQL 真查库
→ 工具结果回喂 → 流式输出带真实数字的回答。

DeepSeek V4 Pro 要求：带工具调用的 assistant 消息必须回传 reasoning_content。
前端维护完整历史（含 tool_calls/tool 结果/reasoning_content），每次请求全量回传，
追问上下文（"那五月呢"）由此成立。done 事件返回更新后的历史供前端保存。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import db as dbmod
from .. import query
from ..ai import client as ai_client
from ..ai import tools as ai_tools

router = APIRouter(prefix="/api")

MAX_HISTORY = 30
MAX_CONTENT = 4000
MAX_TOOL_ROUNDS = 3


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list | None = None
    tool_call_id: str | None = None
    name: str | None = None
    reasoning_content: str | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


def build_system(conn) -> str:
    """系统提示词：动态注入真实数据上下文 + 铁律（数字只来自工具、查不到就说没有）。"""
    bounds = query.data_bounds(conn)
    stores = conn.execute(
        "SELECT store_name, category, district FROM stores ORDER BY store_id"
    ).fetchall()
    store_lines = "、".join(f"{s['store_name']}({s['category']}/{s['district']})" for s in stores)
    scats = "、".join(r["c"] for r in conn.execute(
        "SELECT DISTINCT category AS c FROM stores ORDER BY category"))
    pcats = "、".join(r["c"] for r in conn.execute(
        "SELECT DISTINCT product_category AS c FROM products ORDER BY product_category"))
    payments = "、".join(r["p"] for r in conn.execute(
        "SELECT DISTINCT payment AS p FROM sales ORDER BY payment"))
    year = bounds["date_min"][:4] if bounds["date_min"] else ""

    return (
        f"你是连锁餐饮公司「POKE ONE」的经营数据分析助手，用中文回答。\n\n"
        f"数据范围：{bounds['date_min']} ~ {bounds['date_max']}，已清洗入库 {bounds['sales_rows']} 条销售流水。\n"
        f"门店：{store_lines}。\n门店品类：{scats}。商品品类：{pcats}。支付方式：{payments}。\n\n"
        f"铁律：\n"
        f"1. 任何涉及数字的问题，必须先调用工具查数据库；回答中的数字只能来自工具返回结果，禁止凭记忆或推测编造。\n"
        f"2. 工具查不到（无匹配/空结果）→ 如实说「数据里没有」，可建议换个问法，绝不编造数字。\n"
        f"3. 日期换算：把「六月/上个月/最近30天」等自然语言转成 YYYY-MM-DD 闭区间；"
        f"数据所在年份以数据范围为准（如「六月」= {year}年6月 2026-06-01~2026-06-30）。\n"
        f"4. 门店/商品名用用户原话传给工具参数，由后端模糊匹配；匹配到多个时在回答里说明。\n"
        f"5. 追问时结合对话历史理解指代（如「那五月呢」= 上一个问题的同口径换 5 月）。\n"
        f"6. 回答简洁：先给结论数字，再一句解读；金额单位为元。"
    )


def _assistant_dict(msg) -> dict:
    """把 openai SDK 的 message 转成可回传的 dict（保留 reasoning_content 与 tool_calls）。"""
    d = {"role": "assistant", "content": msg.content or ""}
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tcs
        ]
    rc = getattr(msg, "reasoning_content", None)
    if rc:
        d["reasoning_content"] = rc  # V4 Pro 要求回传，否则 400
    return d


def _tool_summary(result: dict) -> str:
    if not result.get("ok"):
        return f"查询失败：{result.get('error', '未知错误')}"
    data = result.get("data", {})
    if "products" in data:
        n = len(data["products"])
        return f"查询成功：区间 {data.get('start')}~{data.get('end')} Top{n} 商品"
    total = data.get("total", {})
    rows = data.get("rows", [])
    return f"查询成功：区间 {data.get('start')}~{data.get('end')}，共 {len(rows)} 行，"
    f"合计营业额 {total.get('revenue')} 元、订单 {total.get('orders')}"


async def run_chat(messages: list[dict]) -> AsyncIterator[dict]:
    """核心问答流程（可直接测试的异步生成器，产出 SSE 事件 dict）。

    事件：tool（工具执行状态+图表联动 meta）/ delta（流式文本）/ done（更新后的完整历史）。
    """
    conn = dbmod.get_conn()
    try:
        client = ai_client.get_client()
        msgs = [{"role": "system", "content": build_system(conn)}] + messages

        for _ in range(MAX_TOOL_ROUNDS):
            resp = await client.chat.completions.create(
                model=ai_client.MODEL, messages=msgs, tools=ai_tools.TOOL_DEFS
            )
            msg = resp.choices[0].message
            assistant_msg = _assistant_dict(msg)

            if not assistant_msg.get("tool_calls"):
                content = msg.content or ""
                if content:
                    yield {"type": "delta", "data": content}
                # done 回传完整历史（去掉 system）：含本轮工具调用的 assistant 消息与工具结果，
                # 前端保存后全量回传，追问上下文（"那五月呢"）才成立
                yield {"type": "done", "data": {"messages": msgs[1:] + [assistant_msg]}}
                return

            msgs.append(assistant_msg)
            for tc in assistant_msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"] or "{}")
                result = ai_tools.run_tool(tc["function"]["name"], args, conn)
                yield {
                    "type": "tool",
                    "data": {
                        "name": tc["function"]["name"],
                        "args": args,
                        "meta": result.get("meta", {}),
                        "ok": result.get("ok"),
                        "summary": _tool_summary(result),
                    },
                }
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        # 工具轮次用尽 → 不带工具的最终流式回答
        stream = await client.chat.completions.create(
            model=ai_client.MODEL, messages=msgs, stream=True
        )
        final_parts: list[str] = []
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                final_parts.append(delta.content)
                yield {"type": "delta", "data": delta.content}
        final_msg = {"role": "assistant", "content": "".join(final_parts)}
        yield {"type": "done", "data": {"messages": msgs[1:] + [final_msg]}}
    finally:
        conn.close()


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _clean_messages(req: ChatRequest) -> list[dict]:
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")
    if len(req.messages) > MAX_HISTORY:
        raise HTTPException(status_code=400, detail=f"历史消息超过 {MAX_HISTORY} 条上限")
    clean: list[dict] = []
    for m in req.messages:
        if m.role not in ("user", "assistant", "tool"):
            raise HTTPException(status_code=400, detail=f"非法 role: {m.role}")
        d: dict = {"role": m.role}
        if m.role == "tool":
            d["tool_call_id"] = m.tool_call_id or ""
            d["content"] = (m.content or "")[:MAX_CONTENT]
        else:
            if m.content is not None:
                d["content"] = m.content[:MAX_CONTENT]
            if m.tool_calls is not None:
                d["tool_calls"] = m.tool_calls
            if m.reasoning_content is not None:
                d["reasoning_content"] = m.reasoning_content
        clean.append(d)
    if clean[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="最后一条消息必须是 user")
    return clean


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    clean = _clean_messages(req)

    async def gen():
        try:
            async for event in run_chat(clean):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001 — 兜底：明确报错，绝不编造数字
            yield _sse({
                "type": "error",
                "data": {"message": f"AI 服务暂时不可用（{type(exc).__name__}），请稍后重试。"},
            })

    return StreamingResponse(gen(), media_type="text/event-stream")
