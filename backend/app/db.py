"""PostgreSQL 连接层 + schema（v2：由 SQLite 迁移而来，见 README「数据库演进」）。

连接串来自环境变量 DATABASE_URL（每次 get_conn() 现读，方便测试注入临时库）；
默认指向本地开发库（docker 起 postgres:16，见 README）。

迁移取舍：金额/数量列用 DOUBLE PRECISION，保持与迁移前 REAL 一致的浮点行为；
生产演进方向是 NUMERIC 或以分为单位的整数（两者都会改动 API 返回类型，本次未做）。
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "postgresql://postgres:pokeone_dev@localhost:5432/pokeone"

# 逐条执行（psycopg 不支持一次执行多条语句）
SCHEMA_STATEMENTS = [
    # 语义检索（pgvector）：问答历史表依赖此扩展
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS stores (
        store_id   TEXT PRIMARY KEY,
        store_name TEXT NOT NULL,
        category   TEXT NOT NULL DEFAULT '未知',
        district   TEXT NOT NULL DEFAULT '未知'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        product_id       TEXT PRIMARY KEY,
        product_name     TEXT NOT NULL,
        product_category TEXT NOT NULL DEFAULT '未知',
        unit_price       DOUBLE PRECISION NOT NULL DEFAULT 0
    )
    """,
    # 销售流水：清洗后的干净数据，外键保证有效
    """
    CREATE TABLE IF NOT EXISTS sales (
        id         BIGSERIAL PRIMARY KEY,
        order_id   TEXT NOT NULL,
        date       TEXT NOT NULL,   -- ISO YYYY-MM-DD
        store_id   TEXT NOT NULL REFERENCES stores(store_id),
        product_id TEXT NOT NULL REFERENCES products(product_id),
        qty        DOUBLE PRECISION NOT NULL,
        amount     DOUBLE PRECISION NOT NULL,
        payment    TEXT NOT NULL DEFAULT '未知'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date)",
    "CREATE INDEX IF NOT EXISTS idx_sales_store ON sales(store_id)",
    "CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(product_id)",
    # 隔离区：无法修复的脏行，逐行记录原因，供人工复核（透明处理策略）
    """
    CREATE TABLE IF NOT EXISTS quarantined (
        id     BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,   -- sales / stores / products
        reason TEXT NOT NULL,
        raw    TEXT NOT NULL    -- 原始行 JSON
    )
    """,
    # 问答历史（跨会话长期记忆）：embedding 为 bge-small-zh-v1.5 向量（512 维，见 app/ai/embeddings.py）
    """
    CREATE TABLE IF NOT EXISTS qa_history (
        id         BIGSERIAL PRIMARY KEY,
        question   TEXT NOT NULL,
        answer     TEXT NOT NULL,
        embedding  vector(512) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_qa_history_embedding "
    "ON qa_history USING hnsw (embedding vector_cosine_ops)",
]


def get_dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


def get_conn(dsn: str | None = None) -> psycopg.Connection:
    """每次请求新建连接（row_factory=dict_row，按列名取值，与迁移前用法一致）。

    数据量为万级、并发低，直连够用；生产演进可换 psycopg_pool 连接池。
    """
    return psycopg.connect(dsn or get_dsn(), row_factory=dict_row, connect_timeout=10)


def init_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
    conn.commit()


def is_initialized(conn: psycopg.Connection) -> bool:
    """库内是否已有 sales 表（启动时据此决定要不要跑清洗管线）。"""
    row = conn.execute("SELECT to_regclass('public.sales') AS t").fetchone()
    return row["t"] is not None
