"""测试用 PG 工具：建/删临时数据库（各测试文件用独立库，互不干扰）。

DATABASE_URL 指向哪个 PG 就在哪个 PG 上建（本地 docker / CI service 均可）；
建库需能连维护库 postgres 的账号（本地与 CI service 用户都满足）。
"""

import uuid

import psycopg
from psycopg.conninfo import make_conninfo

from app import db as dbmod


def _admin_dsn() -> str:
    """维护库连接串（dbname 换成 postgres），用于 CREATE/DROP DATABASE。"""
    return make_conninfo(dbmod.get_dsn(), dbname="postgres")


def create_temp_db() -> tuple[str, str]:
    """建一个随机名临时库，返回 (指向它的 DSN, 库名)。"""
    name = f"moneki_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return make_conninfo(dbmod.get_dsn(), dbname=name), name


def drop_temp_db(name: str) -> None:
    """删除临时库（WITH (FORCE) 断开残留连接；PG 13+）。"""
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
