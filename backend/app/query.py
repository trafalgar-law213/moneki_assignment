"""★唯一取数层：所有经营指标查询的唯一入口。

看板 API 与 AI 工具都调用这里的函数 → AI 回答的数字与看板数字**由架构保证一致**。
安全：列名只能来自白名单映射；值一律参数化绑定，杜绝 SQL 注入。
"""

from __future__ import annotations

import sqlite3
from datetime import date as date_cls
from datetime import timedelta

# ---------- 常量与白名单 ----------

REVENUE_SQL = "ROUND(SUM(s.amount), 2)"
ORDERS_SQL = "COUNT(DISTINCT s.order_id)"
QTY_SQL = "ROUND(SUM(s.qty), 2)"

# 粒度 → (分组列, JOIN 需求, 排序方式)
_GROUP_COLUMNS = {
    "day": ("s.date", "", "asc"),
    "store": ("s.store_id", "JOIN stores st ON st.store_id = s.store_id", "revenue"),
    "store_category": ("st.category", "JOIN stores st ON st.store_id = s.store_id", "revenue"),
    "product_category": ("p.product_category", "JOIN products p ON p.product_id = s.product_id", "revenue"),
    "product": ("s.product_id", "JOIN products p ON p.product_id = s.product_id", "revenue"),
    "payment": ("s.payment", "", "revenue"),
}

# 粒度 → 额外展示列（与分组列配套的 SELECT 片段）
_GROUP_LABELS = {
    "store": "st.store_name AS label",
    "store_category": "st.category AS label",
    "product_category": "p.product_category AS label",
    "product": "p.product_name AS label",
}

_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
MAX_RANGE_DAYS = 366


# ---------- 基础工具 ----------

def validate_range(start: str, end: str, data_min: str, data_max: str) -> tuple[str, str]:
    """校验并规整日期区间。空值 → 全量范围；非法 → ValueError。"""
    import re

    if start and not re.match(_DATE_RE, start):
        raise ValueError(f"start 日期格式非法: {start}，应为 YYYY-MM-DD")
    if end and not re.match(_DATE_RE, end):
        raise ValueError(f"end 日期格式非法: {end}，应为 YYYY-MM-DD")
    if bool(start) != bool(end):
        raise ValueError("start 与 end 必须同时提供或同时省略")
    start = start or data_min
    end = end or data_max
    if start > end:
        raise ValueError(f"start({start}) 不能晚于 end({end})")
    if (date_cls.fromisoformat(end) - date_cls.fromisoformat(start)).days > MAX_RANGE_DAYS:
        raise ValueError(f"区间超过 {MAX_RANGE_DAYS} 天上限")
    return start, end


def data_bounds(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT MIN(date) AS dmin, MAX(date) AS dmax, COUNT(*) AS rows FROM sales"
    ).fetchone()
    return {"date_min": row["dmin"], "date_max": row["dmax"], "sales_rows": row["rows"]}


def dimensions(conn: sqlite3.Connection) -> dict:
    """维度枚举：门店清单 + 品类/支付方式可选项（看板 /meta 与 AI 提示词共用）。

    枚举属于「数据事实」，只能在这里查一次。路由和提示词里重复写 DISTINCT 查询，
    会增加新品类/支付方式时容易漏改一处（示例：AI 提示词不知道新支付方式 → 查询失败）。
    """
    return {
        "stores": [dict(r) for r in conn.execute(
            "SELECT store_id, store_name, category, district FROM stores ORDER BY store_id")],
        "store_categories": [r["c"] for r in conn.execute(
            "SELECT DISTINCT category AS c FROM stores ORDER BY category")],
        "product_categories": [r["c"] for r in conn.execute(
            "SELECT DISTINCT product_category AS c FROM products ORDER BY product_category")],
        "payments": [r["p"] for r in conn.execute(
            "SELECT DISTINCT payment AS p FROM sales ORDER BY payment")],
    }


def _where(start: str, end: str, store_ids: list[str] | None, extra_sql: str = "") -> tuple[str, list]:
    """构造 WHERE 片段（参数化）。"""
    parts = ["s.date >= ?", "s.date <= ?"]
    params: list = [start, end]
    if store_ids:
        parts.append(f"s.store_id IN ({','.join('?' * len(store_ids))})")
        params.extend(store_ids)
    if extra_sql:
        parts.append(extra_sql)
    return " AND ".join(parts), params


def _round(row: dict, keys: tuple[str, ...]) -> dict:
    for k in keys:
        if row.get(k) is not None:
            row[k] = round(row[k], 2)
    return row


def resolve_stores(conn: sqlite3.Connection, keyword: str) -> list[dict]:
    like = f"%{keyword.strip()}%"
    rows = conn.execute(
        "SELECT store_id, store_name, category, district FROM stores "
        "WHERE store_name LIKE ? OR category LIKE ? OR district LIKE ? "
        "ORDER BY store_id",
        (like, like, like),
    ).fetchall()
    return [dict(r) for r in rows]


def resolve_products(conn: sqlite3.Connection, keyword: str) -> list[dict]:
    """商品名模糊匹配：整词优先，无命中则分词逐个 OR。"""
    kw = keyword.strip()
    rows = conn.execute(
        "SELECT product_id, product_name, product_category, unit_price FROM products "
        "WHERE product_name LIKE ? OR product_category LIKE ? ORDER BY product_id",
        (f"%{kw}%", f"%{kw}%"),
    ).fetchall()
    if rows:
        return [dict(r) for r in rows]
    # 分词回退：'牛肉 poke' → 每个词都试
    import re

    tokens = [t for t in re.split(r"[\s,，/、]+", kw) if t]
    if not tokens:
        return []
    clauses = " OR ".join(["product_name LIKE ?"] * len(tokens))
    params = [f"%{t}%" for t in tokens]
    rows = conn.execute(
        f"SELECT product_id, product_name, product_category, unit_price FROM products "
        f"WHERE {clauses} ORDER BY product_id",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- 指标聚合 ----------

def summary(conn: sqlite3.Connection, start: str, end: str, store_ids: list[str] | None = None) -> dict:
    """区间总览：营业额 / 订单数 / 客单价 + 按日序列 + 上一周期环比。"""
    where, params = _where(start, end, store_ids)
    row = conn.execute(
        f"SELECT {REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders, "
        f"ROUND(SUM(s.amount) * 1.0 / {ORDERS_SQL}, 2) AS aov "
        f"FROM sales s WHERE {where}",
        params,
    ).fetchone()
    by_day = [
        _round(dict(r), ("revenue", "aov"))
        for r in conn.execute(
            f"SELECT s.date, {REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders, "
            f"ROUND(SUM(s.amount) * 1.0 / {ORDERS_SQL}, 2) AS aov "
            f"FROM sales s WHERE {where} GROUP BY s.date ORDER BY s.date",
            params,
        ).fetchall()
    ]
    # 上一周期（紧邻的等长窗口）
    days = (date_cls.fromisoformat(end) - date_cls.fromisoformat(start)).days + 1
    prev_end = date_cls.fromisoformat(start) - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    pwhere, pparams = _where(prev_start.isoformat(), prev_end.isoformat(), store_ids)
    prev = conn.execute(
        f"SELECT {REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders, "
        f"ROUND(SUM(s.amount) * 1.0 / {ORDERS_SQL}, 2) AS aov "
        f"FROM sales s WHERE {pwhere}",
        pparams,
    ).fetchone()

    def _pct(cur, prevv):
        if not prevv:
            return None
        return round((cur - prevv) / prevv * 100, 1)

    cur_rev, cur_ord, cur_aov = row["revenue"] or 0, row["orders"] or 0, row["aov"] or 0
    return {
        "start": start,
        "end": end,
        "revenue": round(cur_rev, 2),
        "orders": cur_ord,
        "aov": round(cur_aov, 2),
        "by_day": by_day,
        "prev_period": {
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
            "revenue": round(prev["revenue"] or 0, 2),
            "orders": prev["orders"] or 0,
            "aov": round(prev["aov"] or 0, 2),
        },
        "change": {
            "revenue_pct": _pct(cur_rev, prev["revenue"]),
            "orders_pct": _pct(cur_ord, prev["orders"]),
            "aov_pct": _pct(cur_aov, prev["aov"]),
        },
    }


def top_products(
    conn: sqlite3.Connection, start: str, end: str, store_ids: list[str] | None = None, limit: int = 10
) -> list[dict]:
    where, params = _where(start, end, store_ids)
    rows = conn.execute(
        f"SELECT s.product_id, p.product_name AS name, p.product_category AS category, "
        f"p.unit_price, {REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders, {QTY_SQL} AS qty "
        f"FROM sales s JOIN products p ON p.product_id = s.product_id "
        f"WHERE {where} GROUP BY s.product_id ORDER BY revenue DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    return [_round(dict(r), ("revenue", "qty")) for r in rows]


def store_comparison(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    where, params = _where(start, end, None)
    rows = conn.execute(
        f"SELECT s.store_id, st.store_name, st.category, st.district, "
        f"{REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders, "
        f"ROUND(SUM(s.amount) * 1.0 / {ORDERS_SQL}, 2) AS aov "
        f"FROM sales s JOIN stores st ON st.store_id = s.store_id "
        f"WHERE {where} GROUP BY s.store_id ORDER BY revenue DESC",
        params,
    ).fetchall()
    return [_round(dict(r), ("revenue", "aov")) for r in rows]


def category_comparison(conn: sqlite3.Connection, start: str, end: str, dim: str) -> list[dict]:
    """dim: store_category（门店品类）或 product_category（商品品类）。"""
    assert dim in ("store_category", "product_category")
    if dim == "store_category":
        join, col = "JOIN stores st ON st.store_id = s.store_id", "st.category"
    else:
        join, col = "JOIN products p ON p.product_id = s.product_id", "p.product_category"
    where, params = _where(start, end, None)
    rows = conn.execute(
        f"SELECT {col} AS category, {REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders "
        f"FROM sales s {join} WHERE {where} GROUP BY {col} ORDER BY revenue DESC",
        params,
    ).fetchall()
    return [_round(dict(r), ("revenue",)) for r in rows]


def anomalies(conn: sqlite3.Connection, start: str, end: str, z_threshold: float = 2.5) -> dict:
    """异常销售预警（同星期对比）：每家店每天只与**同为该星期几**的历史均值/标准差比。

    工作日与工作日比、周末与周末比——排除「周末天然火爆」这类周期性因素，
    只报真正偏离该店该星期水平的日子。桶内样本 <2 天不判定（无统计意义）。
    """
    import statistics

    _WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    where, params = _where(start, end, None)
    rows = conn.execute(
        f"SELECT s.date, s.store_id, st.store_name, {REVENUE_SQL} AS revenue "
        f"FROM sales s JOIN stores st ON st.store_id = s.store_id "
        f"WHERE {where} GROUP BY s.date, s.store_id ORDER BY s.date, s.store_id",
        params,
    ).fetchall()
    per_store: dict[str, list[tuple[str, str, str, float]]] = {}
    for r in rows:
        wd = _WEEKDAYS[date_cls.fromisoformat(r["date"]).weekday()]
        per_store.setdefault(r["store_id"], []).append((r["date"], wd, r["store_name"], r["revenue"]))

    days: list[dict] = []
    by_store: dict[str, dict] = {}
    for sid, series in per_store.items():
        buckets: dict[str, list[float]] = {}
        pos_of: dict[str, int] = {}  # 每个日期在其星期桶内的位置（留一法定位用）
        for date_s, wd, _, rev in series:
            pos_of[date_s] = len(buckets.setdefault(wd, []))
            buckets[wd].append(rev)
        by_store[sid] = {"store_id": sid, "store_name": series[0][2], "anomaly_count": 0}
        for date_s, wd, name, rev in series:
            # 留一法：当天不混入自己的基准（否则异常值会稀释基线、弱化检出）
            baseline = [v for j, v in enumerate(buckets[wd]) if j != pos_of[date_s]]
            if len(baseline) < 2:
                continue  # 该星期几样本不足，无统计意义
            mean_w = statistics.mean(baseline)
            std_w = statistics.stdev(baseline)
            if std_w == 0:
                continue  # 基准全相同（如恒定 100），无法定义偏离
            z = round((rev - mean_w) / std_w, 2)
            if abs(z) >= z_threshold:
                by_store[sid]["anomaly_count"] += 1
                days.append({
                    "date": date_s,
                    "weekday": wd,
                    "store_id": sid,
                    "store_name": name,
                    "revenue": round(rev, 2),
                    "zscore": z,
                    "direction": "偏高" if z > 0 else "偏低",
                    "base_mean": round(mean_w, 2),
                    "pct_vs_base": round((rev - mean_w) / mean_w * 100, 1) if mean_w else 0.0,
                })
    days.sort(key=lambda d: (d["date"], d["store_id"]))
    return {"days": days, "by_store": [v for v in by_store.values()]}


def metrics_by(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    granularity: str,
    store_ids: list[str] | None = None,
    product_ids: list[str] | None = None,
    category: str | None = None,
    limit: int = 20,
) -> dict:
    """AI 工具的通用聚合入口：任意粒度 × 任意过滤组合，全部参数化。"""
    if granularity not in _GROUP_COLUMNS:
        raise ValueError(f"未知粒度: {granularity}")
    group_col, group_join, order = _GROUP_COLUMNS[granularity]

    extra = []
    params_extra: list = []
    joins = group_join
    if product_ids:
        extra.append(f"s.product_id IN ({','.join('?' * len(product_ids))})")
        params_extra.extend(product_ids)
    if category:
        if granularity in ("store", "store_category"):
            if "JOIN stores" not in joins:
                joins += " JOIN stores st ON st.store_id = s.store_id"
            extra.append("st.category = ?")
        else:
            if "JOIN products" not in joins:
                joins += " JOIN products p ON p.product_id = s.product_id"
            extra.append("p.product_category = ?")
        params_extra.append(category)

    where, params = _where(start, end, store_ids, " AND ".join(extra) if extra else "")
    # _where 参数顺序 = [start, end, *store_ids]，extra 子句拼在其后，参数直接追加即可
    params.extend(params_extra)

    label_sql = _GROUP_LABELS.get(granularity, "NULL")
    order_sql = "s.date ASC" if order == "asc" else "revenue DESC"
    limit = max(1, min(int(limit), 50))
    rows = conn.execute(
        f"SELECT {group_col} AS grp, {label_sql}, "
        f"{REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders, {QTY_SQL} AS qty, "
        f"ROUND(SUM(s.amount) * 1.0 / {ORDERS_SQL}, 2) AS aov "
        f"FROM sales s {joins} WHERE {where} "
        f"GROUP BY {group_col} ORDER BY {order_sql} LIMIT ?",
        params + [limit],
    ).fetchall()
    result_rows = []
    for r in rows:
        d = dict(r)
        d["group"] = d.pop("grp")
        _round(d, ("revenue", "qty", "aov"))
        result_rows.append(d)

    total = conn.execute(
        f"SELECT {REVENUE_SQL} AS revenue, {ORDERS_SQL} AS orders, "
        f"ROUND(SUM(s.amount) * 1.0 / {ORDERS_SQL}, 2) AS aov "
        f"FROM sales s {joins} WHERE {where}",
        params,
    ).fetchone()
    return {
        "granularity": granularity,
        "start": start,
        "end": end,
        "rows": result_rows,
        "total": {
            "revenue": round(total["revenue"] or 0, 2),
            "orders": total["orders"] or 0,
            "aov": round(total["aov"] or 0, 2),
        },
    }
