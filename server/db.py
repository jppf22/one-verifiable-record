"""MariaDB connection helpers."""
from contextlib import contextmanager

import mariadb

from config import settings


@contextmanager
def get_conn():
    conn = mariadb.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password,
        database=settings.database_name,
    )
    try:
        yield conn
    finally:
        conn.close()


def query(sql, params=()):
    with get_conn() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount


def insert(sql, params=()):
    """Run an INSERT and return the new row id (AUTO_INCREMENT)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def placeholders(values) -> str:
    """'%s, %s, ...' for an IN (...) list. Callers must ensure values is non-empty."""
    return ", ".join(["%s"] * len(values))
