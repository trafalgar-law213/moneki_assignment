"""测试基建：构造脏数据夹具 CSV → 跑清洗管线 → 注入临时 SQLite。

所有测试共享同一个 session 级测试库（只读使用），APP_DB_PATH 在
import app 之前就位（模块级设置），保证 api/ai 模块拿到测试库。
"""

import os
import tempfile
from pathlib import Path

import pytest

# 模块级：必须在任何 `import app.*` 之前设置
_TEST_DIR = Path(tempfile.mkdtemp(prefix="moneki_test_"))
os.environ["APP_DB_PATH"] = str(_TEST_DIR / "test.db")

DIRTY_STORES_CSV = """store_id,store_name,category,district
S01,人民广场店,拉面,上海·黄浦
S02,静安寺店,轻食,上海·静安
,空ID店,测试,测试
S01,重复店,重复,重复
"""

DIRTY_PRODUCTS_CSV = """product_id,product_name,product_category,unit_price
P01,豚骨拉面,主食,32.0
P02,牛肉poke,主食,42.0
P03,绿茶,饮料,
P03,绿茶重复,饮料,6.0
"""

DIRTY_SALES_CSV = """order_id,date,store_id,product_id,qty,amount,payment
ORD1,2026-07-01,S01,P01,2,64.00,支付宝
ORD1,2026-07-01,S01,P02,1,42.00,支付宝
ORD1,2026-07-01,S01,P01,2,64.00,支付宝
ORD2,2026.7.1,S02,P02,1,42.00,
ORD3,20260702,S02,P03,1,37.00,银行卡
ORD4,2026年7月3日,S01,P01,1,32.00,现金
,2026-07-04,S01,P01,1,32.00,支付宝
ORD5,2026/13/45,S01,P01,1,32.00,支付宝
ORD6,2026-07-05,S99,P01,1,32.00,支付宝
ORD7,2026-07-05,S01,P99,1,32.00,支付宝
ORD8,2026-07-06,S01,P01,1,abc,支付宝
ORD9,2026-07-06,S01,P01,-,32.00,支付宝
ORD10,2026-07-07,S01,P01,1,-64.00,退款
ORD11,2026/07/08,S02,P01,"¥ 2","1,000.00",微信
"""


@pytest.fixture(scope="session", autouse=True)
def test_db(tmp_path_factory) -> dict:
    """建一次测试库，全部测试共享。返回 report 与夹具 CSV 目录。"""
    from app import pipeline

    csv_dir = tmp_path_factory.mktemp("csv")
    (csv_dir / "stores.csv").write_text(DIRTY_STORES_CSV, encoding="utf-8")
    (csv_dir / "products.csv").write_text(DIRTY_PRODUCTS_CSV, encoding="utf-8")
    (csv_dir / "sales.csv").write_text(DIRTY_SALES_CSV, encoding="utf-8")

    report = pipeline.run(data_dir=csv_dir, db_path=Path(os.environ["APP_DB_PATH"]))
    return {"report": report, "data_dir": csv_dir}
