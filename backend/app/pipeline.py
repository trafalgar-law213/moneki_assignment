"""CSV → SQLite 数据清洗管线。

设计决策（与 README「数据处理策略」、DEVLOG、AI_USAGE 对应）：

1. 脏数据不静默删除：无法修复的行进入 quarantined 隔离表，逐行记录原因，
   质量报告（backend/data/quality_report.json）公开全部清洗数字，可审计。
2. 完全重复的行去重；同一 order_id 出现多行不同内容视为**正常多行订单**
   （POS 一单多商品），保留，不误删。
3. 缺失/无法解析的日期、金额 → 隔离。
4. 脏外键（指向不存在的门店/商品）→ 隔离。
5. 缺失的 unit_price 用同品类中位数填充（品类也缺则用全局中位数）；空维度填「未知」。
6. 负金额（退款）保留原值——退款是经营事实，直接计入营业额。
7. 幂等：每次运行重建数据库（1.2 万行重建毫秒级）。

用法：`python -m app.pipeline`（在 backend/ 目录下）
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path
from statistics import median

import psycopg

from . import db as dbmod

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", REPO_ROOT / "data"))
# 质量报告固定写 backend/data/（只含聚合数字，可审计；迁移后与数据库位置解耦）
REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "quality_report.json"

_NUM_STRIP = re.compile(r"[¥￥,\s元]")

# 生产导出日期格式不一，逐个尝试；group(0) 即日期主体（带时间的也能截出日期）
_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})"), "%Y-%m-%d"),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})"), "%Y/%m/%d"),
    (re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})"), "%Y.%m.%d"),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), "%Y%m%d"),
    (re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日"), "%Y年%m月%d日"),
]


def parse_date(raw: object) -> str | None:
    """把各种格式的日期规整为 ISO YYYY-MM-DD；无法解析返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.match(text)
        if m:
            try:
                return datetime.strptime(m.group(0), fmt).date().isoformat()
            except ValueError:
                return None
    return None


def parse_number(raw: object) -> float | None:
    """金额/数量规整为 float：容忍 ¥ 符号、千分位逗号、全角空格。"""
    if raw is None:
        return None
    text = _NUM_STRIP.sub("", str(raw)).strip()
    if not text or text in ("-", "nan", "None", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clean_text(raw: object) -> str:
    text = str(raw or "").strip()
    return text if text else "未知"


def load_csv(path: Path) -> list[dict]:
    """读取 CSV，容忍 BOM / GBK / UTF-8 编码差异，列名统一小写去空格。"""
    raw_bytes = path.read_bytes()
    for encoding in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{path.name} 编码无法识别（试过 utf-8-sig/gbk/utf-8）")
    reader = csv.DictReader(io.StringIO(text))
    return [{k.strip().lower(): v for k, v in row.items()} for row in reader]


def clean_stores(rows: list[dict]) -> tuple[dict[str, dict], dict]:
    report = {"rows_read": len(rows), "kept": 0, "empty_id": 0, "duplicate_id": 0}
    stores: dict[str, dict] = {}
    for row in rows:
        sid = str(row.get("store_id") or "").strip()
        if not sid:
            report["empty_id"] += 1
            continue
        if sid in stores:
            report["duplicate_id"] += 1
            continue
        stores[sid] = {
            "store_name": clean_text(row.get("store_name")),
            "category": clean_text(row.get("category")),
            "district": clean_text(row.get("district")),
        }
    report["kept"] = len(stores)
    return stores, report


def clean_products(rows: list[dict]) -> tuple[dict[str, dict], dict]:
    report = {
        "rows_read": len(rows),
        "kept": 0,
        "empty_id": 0,
        "duplicate_id": 0,
        "unit_price_filled": 0,
    }
    products: dict[str, dict] = {}
    for row in rows:
        pid = str(row.get("product_id") or "").strip()
        if not pid:
            report["empty_id"] += 1
            continue
        if pid in products:
            report["duplicate_id"] += 1
            continue
        products[pid] = {
            "product_name": clean_text(row.get("product_name")),
            "product_category": clean_text(row.get("product_category")),
            "unit_price": parse_number(row.get("unit_price")),
        }
    # 缺失单价：同品类中位数 → 全局中位数 → 0
    valid_prices = [p["unit_price"] for p in products.values() if p["unit_price"] is not None]
    global_median = median(valid_prices) if valid_prices else 0.0
    by_category: dict[str, list[float]] = {}
    for p in products.values():
        if p["unit_price"] is not None:
            by_category.setdefault(p["product_category"], []).append(p["unit_price"])
    for p in products.values():
        if p["unit_price"] is None:
            p["unit_price"] = (
                median(by_category[p["product_category"]])
                if by_category.get(p["product_category"])
                else global_median
            )
            report["unit_price_filled"] += 1
    report["kept"] = len(products)
    return products, report


def clean_sales(
    rows: list[dict], stores: dict[str, dict], products: dict[str, dict]
) -> tuple[list[dict], list[tuple[str, str]], dict]:
    report = {
        "rows_read": len(rows),
        "kept": 0,
        "exact_duplicates": 0,
        "missing_order_id": 0,
        "bad_date": 0,
        "bad_amount": 0,
        "bad_qty_zeroed": 0,
        "dirty_fk": 0,
        "negative_amount_rows": 0,
    }
    clean: list[dict] = []
    quarantine: list[tuple[str, str]] = []
    seen_exact: set[tuple] = set()

    for row in rows:
        raw_json = json.dumps(row, ensure_ascii=False)

        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            report["missing_order_id"] += 1
            quarantine.append(("order_id 缺失", raw_json))
            continue

        date_iso = parse_date(row.get("date"))
        if date_iso is None:
            report["bad_date"] += 1
            quarantine.append(("日期无法解析", raw_json))
            continue

        store_id = str(row.get("store_id") or "").strip()
        product_id = str(row.get("product_id") or "").strip()
        if store_id not in stores or product_id not in products:
            report["dirty_fk"] += 1
            quarantine.append((f"脏外键 store={store_id!r} product={product_id!r}", raw_json))
            continue

        amount = parse_number(row.get("amount"))
        if amount is None:
            report["bad_amount"] += 1
            quarantine.append(("金额无法解析", raw_json))
            continue

        qty = parse_number(row.get("qty"))
        if qty is None:
            report["bad_qty_zeroed"] += 1
            qty = 0.0

        if amount < 0:
            report["negative_amount_rows"] += 1

        payment = clean_text(row.get("payment"))
        # 多行订单（同 order_id 不同内容）是 POS 正常现象，只有完全相同的行才算重复
        key = (order_id, date_iso, store_id, product_id, qty, amount, payment)
        if key in seen_exact:
            report["exact_duplicates"] += 1
            continue
        seen_exact.add(key)

        clean.append({
            "order_id": order_id,
            "date": date_iso,
            "store_id": store_id,
            "product_id": product_id,
            "qty": qty,
            "amount": amount,
            "payment": payment,
        })

    report["kept"] = len(clean)
    return clean, quarantine, report


def write_db(
    conn: psycopg.Connection,
    stores: dict[str, dict],
    products: dict[str, dict],
    sales: list[dict],
    quarantine: list[tuple[str, str]],
) -> None:
    dbmod.init_schema(conn)
    for table in ("quarantined", "sales", "products", "stores"):
        conn.execute(f"DELETE FROM {table}")
    # psycopg 的命名参数为 %(name)s（SQLite 的 :name 语法不适用）
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO stores(store_id, store_name, category, district) "
            "VALUES(%(store_id)s, %(store_name)s, %(category)s, %(district)s)",
            [{"store_id": sid, **meta} for sid, meta in stores.items()],
        )
        cur.executemany(
            "INSERT INTO products(product_id, product_name, product_category, unit_price) "
            "VALUES(%(product_id)s, %(product_name)s, %(product_category)s, %(unit_price)s)",
            [{"product_id": pid, **meta} for pid, meta in products.items()],
        )
        cur.executemany(
            "INSERT INTO sales(order_id, date, store_id, product_id, qty, amount, payment) "
            "VALUES(%(order_id)s, %(date)s, %(store_id)s, %(product_id)s, %(qty)s, %(amount)s, %(payment)s)",
            sales,
        )
        cur.executemany(
            "INSERT INTO quarantined(source, reason, raw) VALUES('sales', %s, %s)",
            quarantine,
        )
    conn.commit()


def run(data_dir: Path | None = None, dsn: str | None = None) -> dict:
    """完整跑一次清洗入库，返回质量报告（幂等，可重复执行）。"""
    data_dir = Path(data_dir or DATA_DIR)

    missing = [f.name for f in ("sales.csv", "stores.csv", "products.csv") if not (data_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"数据文件缺失: {missing}，请放入 {data_dir}")

    stores, stores_report = clean_stores(load_csv(data_dir / "stores.csv"))
    products, products_report = clean_products(load_csv(data_dir / "products.csv"))
    sales, quarantine, sales_report = clean_sales(load_csv(data_dir / "sales.csv"), stores, products)

    conn = dbmod.get_conn(dsn)
    write_db(conn, stores, products, sales, quarantine)

    row = conn.execute("SELECT MIN(date) AS dmin, MAX(date) AS dmax, COUNT(*) AS n FROM sales").fetchone()
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "sales.csv": {"rows_read": sales_report["rows_read"]},
            "stores.csv": {"rows_read": stores_report["rows_read"]},
            "products.csv": {"rows_read": products_report["rows_read"]},
        },
        "cleaning": {"stores": stores_report, "products": products_report, "sales": sales_report},
        "db": {
            "sales_rows": row["n"],
            "quarantined_rows": len(quarantine),
            "date_min": row["dmin"],
            "date_max": row["dmax"],
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    conn.close()
    return report


def print_summary(report: dict) -> None:
    """只打印聚合数字，不打印原始行。"""
    c = report["cleaning"]["sales"]
    s, p = report["cleaning"]["stores"], report["cleaning"]["products"]
    db = report["db"]
    print(f"[pipeline] sales 读入 {c['rows_read']} 行 → 入库 {c['kept']} 行")
    print(f"[pipeline] 完全重复 {c['exact_duplicates']} | 缺order_id {c['missing_order_id']} | "
          f"坏日期 {c['bad_date']} | 坏金额 {c['bad_amount']} | 坏数量置0 {c['bad_qty_zeroed']} | "
          f"脏外键 {c['dirty_fk']} | 负金额行 {c['negative_amount_rows']}")
    print(f"[pipeline] 门店 {s['kept']} 家(重id {s['duplicate_id']}, 空id {s['empty_id']}) | "
          f"商品 {p['kept']} 个(重id {p['duplicate_id']}, 补单价 {p['unit_price_filled']})")
    print(f"[pipeline] 隔离区 {db['quarantined_rows']} 行 | 日期范围 {db['date_min']} ~ {db['date_max']}")


if __name__ == "__main__":
    print_summary(run())
