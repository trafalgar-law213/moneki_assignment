"""看板 API 测试：接口数字与手工计算的期望值对账。

夹具数据（tests/conftest.py 清洗后入库 8 行）：
  ORD1×2行 S01 P01 64 + P02 42 | ORD2 S02 P02 42 | ORD3 S02 P03 37
  ORD4 S01 P01 32 | ORD9 S01 P01 32 | ORD10 S01 P01 -64 | ORD11 S02 P01 1000
全量(07-01~07-08)：营业额 1185、订单 7、客单价 169.29
"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app import query
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["date_min"] == "2026-07-01"
    assert body["date_max"] == "2026-07-08"
    assert body["sales_rows"] == 8


def test_meta():
    body = client.get("/api/meta").json()
    assert [s["store_id"] for s in body["stores"]] == ["S01", "S02"]
    assert body["store_categories"] == ["拉面", "轻食"]
    assert set(body["product_categories"]) == {"主食", "饮料"}
    assert len(body["payments"]) == 6  # 支付宝/银行卡/现金/退款/微信/未知


def test_summary_full_range():
    body = client.get("/api/dashboard/summary").json()
    assert body["revenue"] == 1185.0
    assert body["orders"] == 7
    assert body["aov"] == 169.29
    assert len(body["by_day"]) == 6
    d1 = next(d for d in body["by_day"] if d["date"] == "2026-07-01")
    # 07-01：ORD1(两行商品) + ORD2 → 2 个订单，148/2 = 74.0
    assert d1["revenue"] == 148.0 and d1["orders"] == 2 and d1["aov"] == 74.0
    d7 = next(d for d in body["by_day"] if d["date"] == "2026-07-07")
    assert d7["revenue"] == -64.0  # 退款日
    # 上一周期窗口(06-25~06-30)无数据
    assert body["prev_period"]["revenue"] == 0.0
    assert body["change"]["revenue_pct"] is None


def test_summary_sub_range():
    body = client.get("/api/dashboard/summary", params={"start": "2026-07-03", "end": "2026-07-08"}).json()
    assert body["revenue"] == 1000.0   # 32 + 32 - 64 + 1000
    assert body["orders"] == 4
    assert body["aov"] == 250.0


def test_summary_store_filter():
    body = client.get("/api/dashboard/summary", params={"store_id": "S01"}).json()
    assert body["revenue"] == 106.0   # 64 + 42 + 32 + 32 - 64
    assert body["orders"] == 4
    assert body["aov"] == 26.5


def test_top_products_ordering():
    body = client.get("/api/dashboard/top-products").json()
    assert [p["product_id"] for p in body] == ["P01", "P02", "P03"]
    assert body[0]["revenue"] == 1064.0   # 64+32+32-64+1000
    assert body[0]["orders"] == 5
    assert body[0]["qty"] == 6.0
    assert body[1]["revenue"] == 84.0
    assert body[2]["revenue"] == 37.0


def test_stores_comparison():
    body = client.get("/api/dashboard/stores").json()
    assert [s["store_id"] for s in body] == ["S02", "S01"]  # 按营业额降序
    assert body[0]["revenue"] == 1079.0
    assert body[0]["orders"] == 3
    assert body[1]["revenue"] == 106.0


def test_categories():
    body = client.get("/api/dashboard/categories").json()
    assert body["store_categories"][0]["category"] == "轻食"
    assert body["store_categories"][0]["revenue"] == 1079.0
    assert body["product_categories"][0]["revenue"] == 1148.0  # 主食


def test_anomalies_empty_on_fixture():
    """夹具数据波动小，z 阈 2.5 不应触发（逻辑正确性见下方定向测试）。"""
    body = client.get("/api/dashboard/anomalies").json()
    assert body["days"] == []
    assert len(body["by_store"]) == 2


def test_invalid_date_400():
    r = client.get("/api/dashboard/summary", params={"start": "07-01"})
    assert r.status_code == 400


def test_invalid_range_400():
    r = client.get("/api/dashboard/summary", params={"start": "2026-07-08", "end": "2026-07-01"})
    assert r.status_code == 400


# ---------- 异常预警定向测试（独立临时库，精确构造数据） ----------

def test_anomalies_detection_logic(tmp_path):
    """10 天营收 100 + 1 天 5000 → 该日 z≈3.0 ≥ 2.5 必须被标记；平稳门店不误报。"""
    db_path = tmp_path / "anomaly.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    dbmod.init_schema(conn)
    conn.executemany(
        "INSERT INTO stores(store_id, store_name, category, district) VALUES(?,?,?,?)",
        [("S01", "测试店A", "拉面", "测试"), ("S02", "测试店B", "轻食", "测试")],
    )
    rows = []
    for day in range(1, 12):  # 07-01 ~ 07-11
        date_s = f"2026-07-{day:02d}"
        rows.append((f"A{day}", date_s, "S01", "P01", 1, 5000.0 if day == 11 else 100.0, "现金"))
        rows.append((f"B{day}", date_s, "S02", "P01", 1, 100.0, "现金"))
    conn.executemany(
        "INSERT INTO sales(order_id, date, store_id, product_id, qty, amount, payment) "
        "VALUES(?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()

    result = query.anomalies(conn, "2026-07-01", "2026-07-11")
    flagged = [d for d in result["days"] if d["store_id"] == "S01"]
    assert len(flagged) == 1
    assert flagged[0]["date"] == "2026-07-11"
    assert flagged[0]["direction"] == "偏高"
    assert flagged[0]["zscore"] >= 2.5
    # S02 平稳无异常
    assert all(d["store_id"] != "S02" for d in result["days"])
    conn.close()


# ---------- 取数层纯函数 ----------

def test_validate_range():
    assert query.validate_range(None, None, "2026-05-01", "2026-07-31") == ("2026-05-01", "2026-07-31")
    assert query.validate_range("2026-06-01", "2026-06-30", "2026-05-01", "2026-07-31") == ("2026-06-01", "2026-06-30")
    with pytest.raises(ValueError):
        query.validate_range("2026-06-01", None, "2026-05-01", "2026-07-31")
    with pytest.raises(ValueError):
        query.validate_range("2026-07-01", "2026-06-01", "2026-05-01", "2026-07-31")


def test_resolve_products_keyword():
    conn = dbmod.get_conn()
    hits = query.resolve_products(conn, "poke")
    assert {p["product_id"] for p in hits} == {"P02"}  # 夹具里只有 P02 牛肉poke
    hits2 = query.resolve_products(conn, "不存在的商品")
    assert hits2 == []


def test_metrics_by_granularities():
    conn = dbmod.get_conn()
    by_store = query.metrics_by(conn, "2026-07-01", "2026-07-08", "store")
    assert by_store["total"]["revenue"] == 1185.0
    assert by_store["rows"][0]["group"] == "S02"  # 按营业额降序
    by_day = query.metrics_by(conn, "2026-07-01", "2026-07-08", "day")
    assert len(by_day["rows"]) == 6
    with pytest.raises(ValueError):
        query.metrics_by(conn, "2026-07-01", "2026-07-08", "evil; DROP TABLE sales")
