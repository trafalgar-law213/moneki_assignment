"""SQLite 连接层 + schema。

数据库文件默认 backend/data/app.db，可用环境变量 APP_DB_PATH 覆盖。
环境变量在每次 get_conn() 时读取（而非 import 时），方便测试注入临时库。
"""

import os
import sqlite3
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "app.db"


def get_db_path() -> Path:
    return Path(os.environ.get("APP_DB_PATH", _DEFAULT_DB))


SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    store_id   TEXT PRIMARY KEY,
    store_name TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT '未知',
    district   TEXT NOT NULL DEFAULT '未知'
);

CREATE TABLE IF NOT EXISTS products (
    product_id       TEXT PRIMARY KEY,
    product_name     TEXT NOT NULL,
    product_category TEXT NOT NULL DEFAULT '未知',
    unit_price       REAL NOT NULL DEFAULT 0
);

-- 销售流水：清洗后的干净数据，外键保证有效
CREATE TABLE IF NOT EXISTS sales (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   TEXT NOT NULL,
    date       TEXT NOT NULL,   -- ISO YYYY-MM-DD
    store_id   TEXT NOT NULL REFERENCES stores(store_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    qty        REAL NOT NULL,
    amount     REAL NOT NULL,
    payment    TEXT NOT NULL DEFAULT '未知'
);
CREATE INDEX IF NOT EXISTS idx_sales_date    ON sales(date);
CREATE INDEX IF NOT EXISTS idx_sales_store   ON sales(store_id);
CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(product_id);

-- 隔离区：无法修复的脏行，逐行记录原因，供人工复核（透明处理策略）
CREATE TABLE IF NOT EXISTS quarantined (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,   -- sales / stores / products
    reason TEXT NOT NULL,
    raw    TEXT NOT NULL    -- 原始行 JSON
);
"""


def get_conn() -> sqlite3.Connection:
    """每次请求新建连接。SQLite 读并发安全，WAL 模式进一步降低锁冲突。"""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
