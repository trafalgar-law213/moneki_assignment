"""看板 API：第一关全部接口 + 门店对比/异常预警进阶接口。

所有数字走 query.py 唯一取数层，与 AI 工具共享同一套聚合逻辑。
"""

from fastapi import APIRouter, HTTPException

from .. import db as dbmod
from .. import query

router = APIRouter(prefix="/api")


def _range_or_full(start: str | None, end: str | None, conn) -> tuple[str, str]:
    bounds = query.data_bounds(conn)
    return query.validate_range(start, end, bounds["date_min"], bounds["date_max"])


@router.get("/health")
def health():
    conn = dbmod.get_conn()
    try:
        return {"ok": True, **query.data_bounds(conn)}
    finally:
        conn.close()


@router.get("/meta")
def meta():
    conn = dbmod.get_conn()
    try:
        stores = [dict(r) for r in conn.execute(
            "SELECT store_id, store_name, category, district FROM stores ORDER BY store_id"
        )]
        return {
            **query.data_bounds(conn),
            "stores": stores,
            "store_categories": [r["c"] for r in conn.execute(
                "SELECT DISTINCT category AS c FROM stores ORDER BY category")],
            "product_categories": [r["c"] for r in conn.execute(
                "SELECT DISTINCT product_category AS c FROM products ORDER BY product_category")],
            "payments": [r["p"] for r in conn.execute(
                "SELECT DISTINCT payment AS p FROM sales ORDER BY payment")],
        }
    finally:
        conn.close()


@router.get("/dashboard/summary")
def dashboard_summary(start: str | None = None, end: str | None = None, store_id: str | None = None):
    conn = dbmod.get_conn()
    try:
        s, e = _range_or_full(start, end, conn)
        return query.summary(conn, s, e, [store_id] if store_id else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@router.get("/dashboard/top-products")
def top_products(
    start: str | None = None, end: str | None = None, store_id: str | None = None, limit: int = 10
):
    conn = dbmod.get_conn()
    try:
        s, e = _range_or_full(start, end, conn)
        return query.top_products(conn, s, e, [store_id] if store_id else None, min(limit, 20))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@router.get("/dashboard/stores")
def stores(start: str | None = None, end: str | None = None):
    conn = dbmod.get_conn()
    try:
        s, e = _range_or_full(start, end, conn)
        return query.store_comparison(conn, s, e)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@router.get("/dashboard/categories")
def categories(start: str | None = None, end: str | None = None):
    conn = dbmod.get_conn()
    try:
        s, e = _range_or_full(start, end, conn)
        return {
            "store_categories": query.category_comparison(conn, s, e, "store_category"),
            "product_categories": query.category_comparison(conn, s, e, "product_category"),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@router.get("/dashboard/anomalies")
def anomalies(start: str | None = None, end: str | None = None):
    conn = dbmod.get_conn()
    try:
        s, e = _range_or_full(start, end, conn)
        return query.anomalies(conn, s, e)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
