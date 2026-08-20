"""AI 工具注册表：LLM 出结构化参数，这里拼白名单 SQL 真查库。

解耦设计：每个工具 = 独立函数 + 独立注册；新增工具只加函数、改 TOOL_IMPL，
不动聊天链路。所有数字走 query.py 唯一取数层（与看板 API 同源）。
"""

from __future__ import annotations

import sqlite3

from .. import query

# ---------- 工具定义（DeepSeek OpenAI 兼容格式） ----------

QUERY_METRICS_DEF = {
    "type": "function",
    "function": {
        "name": "query_metrics",
        "description": (
            "查询真实经营指标（营业额/订单数/客单价/销量）。"
            "start_date/end_date 为闭区间 YYYY-MM-DD。granularity 决定分组维度："
            "day=按日趋势, store=按门店, store_category=按门店品类, "
            "product_category=按商品品类, product=按商品, payment=按支付方式。"
            "store_name/product_name 用用户原话传入，由后端模糊匹配；category 传品类精确名。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "granularity": {
                    "type": "string",
                    "enum": ["day", "store", "store_category", "product_category", "product", "payment"],
                },
                "store_name": {"type": "string", "description": "门店名/品类/区名关键词（可选）"},
                "product_name": {"type": "string", "description": "商品名关键词（可选），如 '牛肉poke'"},
                "category": {"type": "string", "description": "品类名精确值（可选），如 '轻食'"},
                "limit": {"type": "integer", "description": "返回前 N 行（默认 20，最大 50）"},
            },
            "required": ["start_date", "end_date", "granularity"],
        },
    },
}

QUERY_TOP_PRODUCTS_DEF = {
    "type": "function",
    "function": {
        "name": "query_top_products",
        "description": "查询区间内营业额最高的前 N 个商品（含销量、订单数、单价）。",
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "store_name": {"type": "string", "description": "门店名关键词（可选）"},
                "limit": {"type": "integer", "description": "前 N 名（默认 10，最大 20）"},
            },
            "required": ["start_date", "end_date"],
        },
    },
}

RESOLVE_PRODUCTS_DEF = {
    "type": "function",
    "function": {
        "name": "resolve_products",
        "description": "按关键词查商品维表（商品名/品类模糊匹配），用于确认用户说的是哪个商品。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "商品名或品类关键词，如 'poke'"},
            },
            "required": ["keyword"],
        },
    },
}

TOOL_DEFS = [QUERY_METRICS_DEF, QUERY_TOP_PRODUCTS_DEF, RESOLVE_PRODUCTS_DEF]


# ---------- 工具实现 ----------

def _range(conn: sqlite3.Connection, args: dict) -> tuple[str, str]:
    bounds = query.data_bounds(conn)
    return query.validate_range(args["start_date"], args["end_date"], bounds["date_min"], bounds["date_max"])


def _all_stores(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT store_id, store_name FROM stores ORDER BY store_id")]


def tool_query_metrics(conn: sqlite3.Connection, args: dict) -> dict:
    try:
        start, end = _range(conn, args)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "meta": {}}

    store_ids, matched_stores = None, []
    if args.get("store_name"):
        matched_stores = query.resolve_stores(conn, args["store_name"])
        if not matched_stores:
            avail = "、".join(s["store_name"] for s in _all_stores(conn))
            return {"ok": False, "error": f"没有匹配「{args['store_name']}」的门店。可选门店：{avail}", "meta": {}}
        store_ids = [s["store_id"] for s in matched_stores]

    product_ids, matched_products = None, []
    if args.get("product_name"):
        matched_products = query.resolve_products(conn, args["product_name"])
        if not matched_products:
            return {"ok": False, "error": f"没有匹配「{args['product_name']}」的商品。可用 resolve_products 查商品清单。", "meta": {}}
        product_ids = [p["product_id"] for p in matched_products]

    data = query.metrics_by(
        conn, start, end, args["granularity"], store_ids, product_ids,
        args.get("category"), args.get("limit", 20),
    )
    return {
        "ok": True,
        "data": data,
        "matched_stores": [s["store_name"] for s in matched_stores],
        "matched_products": [p["product_name"] for p in matched_products],
        "meta": {
            "start": start, "end": end, "granularity": args["granularity"],
            "store_ids": store_ids, "product_ids": product_ids,
        },
    }


def tool_query_top_products(conn: sqlite3.Connection, args: dict) -> dict:
    try:
        start, end = _range(conn, args)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "meta": {}}

    store_ids = None
    if args.get("store_name"):
        matched = query.resolve_stores(conn, args["store_name"])
        if not matched:
            avail = "、".join(s["store_name"] for s in _all_stores(conn))
            return {"ok": False, "error": f"没有匹配「{args['store_name']}」的门店。可选门店：{avail}", "meta": {}}
        store_ids = [s["store_id"] for s in matched]

    products = query.top_products(conn, start, end, store_ids, min(int(args.get("limit", 10)), 20))
    return {
        "ok": True,
        "data": {"start": start, "end": end, "products": products},
        "meta": {"start": start, "end": end, "store_ids": store_ids},
    }


def tool_resolve_products(conn: sqlite3.Connection, args: dict) -> dict:
    hits = query.resolve_products(conn, args["keyword"])
    return {
        "ok": True,
        "data": {"keyword": args["keyword"], "products": hits},
        "meta": {},
        "hint": "返回空列表说明没有该商品" if not hits else None,
    }


TOOL_IMPL = {
    "query_metrics": tool_query_metrics,
    "query_top_products": tool_query_top_products,
    "resolve_products": tool_resolve_products,
}


def run_tool(name: str, args: dict, conn: sqlite3.Connection) -> dict:
    """执行工具；任何异常都转成结构化错误（让模型如实说明，不编造）。"""
    impl = TOOL_IMPL.get(name)
    if impl is None:
        return {"ok": False, "error": f"未知工具: {name}", "meta": {}}
    try:
        return impl(conn, args)
    except Exception as exc:  # noqa: BLE001 — 兜底必须全捕
        return {"ok": False, "error": f"工具执行失败: {type(exc).__name__}", "meta": {}}
