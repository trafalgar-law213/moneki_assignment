"""数据清洗管线单元测试：8 类脏数据处理规则逐一断言。"""

import pytest

from app import db as dbmod
from app import pipeline


# ---------- 解析函数纯单元测试 ----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-07-01", "2026-07-01"),
        ("2026/7/1", "2026-07-01"),
        ("2026.7.1", "2026-07-01"),
        ("20260702", "2026-07-02"),
        ("2026年7月3日", "2026-07-03"),
        ("2026-07-04 12:30:00", "2026-07-04"),  # 带时间戳
        ("2026/13/45", None),                    # 非法月日
        ("昨天", None),
        (None, None),
    ],
)
def test_parse_date(raw, expected):
    assert pipeline.parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("36.00", 36.0),
        ("¥ 36", 36.0),
        ("1,000.00", 1000.0),
        ("３６．５", None),   # 全角数字不认 → 隔离而非误读
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_number(raw, expected):
    assert pipeline.parse_number(raw) == expected


# ---------- 端到端清洗行为（夹具数据见 tests/conftest.py） ----------

@pytest.fixture(scope="module")
def env(test_db):
    return test_db


def test_report_counts(env):
    report = env["report"]
    c = report["cleaning"]["sales"]
    assert c["rows_read"] == 14
    assert c["kept"] == 8            # 14 - 1完全重复 - 1缺id - 1坏日期 - 2脏FK - 1坏金额 = 8
    assert c["exact_duplicates"] == 1
    assert c["missing_order_id"] == 1
    assert c["bad_date"] == 1
    assert c["dirty_fk"] == 2
    assert c["bad_amount"] == 1
    assert c["bad_qty_zeroed"] == 1
    assert c["negative_amount_rows"] == 1
    assert report["cleaning"]["stores"]["kept"] == 2
    assert report["cleaning"]["stores"]["empty_id"] == 1
    assert report["cleaning"]["stores"]["duplicate_id"] == 1
    assert report["cleaning"]["products"]["kept"] == 3
    assert report["cleaning"]["products"]["unit_price_filled"] == 1
    assert report["db"]["sales_rows"] == 8
    assert report["db"]["quarantined_rows"] == 5


def test_quarantined_rows_have_reasons():
    conn = dbmod.get_conn()
    rows = conn.execute(
        "SELECT reason, raw FROM quarantined ORDER BY id"
    ).fetchall()
    reasons = " | ".join(r["reason"] for r in rows)
    assert "order_id 缺失" in reasons
    assert "日期无法解析" in reasons
    assert "脏外键" in reasons and reasons.count("脏外键") == 2
    assert "金额无法解析" in reasons
    assert all("ORD" in r["raw"] or "order_id" in r["raw"] for r in rows)


def test_multiline_order_kept():
    """同一 order_id 的多行不同内容 = 正常多行订单，必须保留。"""
    conn = dbmod.get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM sales WHERE order_id='ORD1'").fetchone()["n"]
    assert n == 2


def test_dirty_rows_absent_from_sales():
    conn = dbmod.get_conn()
    ids = {r["order_id"] for r in conn.execute("SELECT order_id FROM sales")}
    for bad in ("ORD5", "ORD6", "ORD7", "ORD8"):
        assert bad not in ids


def test_date_normalization():
    conn = dbmod.get_conn()
    dates = {r["order_id"]: r["date"] for r in conn.execute("SELECT order_id, date FROM sales")}
    assert dates["ORD2"] == "2026-07-01"   # 2026.7.1
    assert dates["ORD3"] == "2026-07-02"   # 20260702
    assert dates["ORD4"] == "2026-07-03"   # 2026年7月3日
    assert dates["ORD11"] == "2026-07-08"  # 2026/07/08


def test_empty_payment_filled():
    conn = dbmod.get_conn()
    payment = conn.execute("SELECT payment FROM sales WHERE order_id='ORD2'").fetchone()["payment"]
    assert payment == "未知"


def test_bad_qty_zeroed():
    conn = dbmod.get_conn()
    qty = conn.execute("SELECT qty FROM sales WHERE order_id='ORD9'").fetchone()["qty"]
    assert qty == 0.0


def test_negative_refund_kept():
    conn = dbmod.get_conn()
    amount = conn.execute("SELECT amount FROM sales WHERE order_id='ORD10'").fetchone()["amount"]
    assert amount == -64.0


def test_currency_and_thousands_parsed():
    conn = dbmod.get_conn()
    row = conn.execute("SELECT qty, amount FROM sales WHERE order_id='ORD11'").fetchone()
    assert row["qty"] == 2.0
    assert row["amount"] == 1000.0


def test_unit_price_filled_with_global_median():
    """P03（饮料，无同品类有效价）→ 全局中位数 median(32,42)=37.0。"""
    conn = dbmod.get_conn()
    price = conn.execute("SELECT unit_price FROM products WHERE product_id='P03'").fetchone()["unit_price"]
    assert price == 37.0


def test_idempotent_rerun(env):
    """重复执行结果一致（幂等重建）。"""
    conn = dbmod.get_conn()
    before = conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"]
    report2 = pipeline.run(data_dir=env["data_dir"])
    after = conn.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"]
    assert before == after == report2["db"]["sales_rows"]
