"""Postgres access for the shared memory store (Neon + pgvector)."""

from __future__ import annotations

import atexit
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

SCHEMA_PATH = Path(__file__).resolve().parent / 'schema.sql'

_pool = None
_pool_lock = threading.Lock()


def database_url() -> str:
    # Neon's Marketplace integration injects DATABASE_URL (and POSTGRES_URL).
    return (os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL') or '').strip()


def configured() -> bool:
    return bool(database_url())


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            url = database_url()
            if not url:
                raise RuntimeError('DATABASE_URL is not configured.')
            _pool = ConnectionPool(
                url,
                min_size=0,
                max_size=4,
                open=True,
                timeout=10,
                kwargs={
                    'row_factory': dict_row,
                    'autocommit': False,
                    # Neon's pooler runs PgBouncer in transaction mode, which
                    # does not support server-side prepared statements.
                    'prepare_threshold': None,
                    'connect_timeout': 10,
                },
            )
    return _pool


def reset_pool_for_tests() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool = None


atexit.register(reset_pool_for_tests)


@contextmanager
def transaction() -> Iterator[Any]:
    """Yield a connection inside a transaction; commits on success."""
    pool = _get_pool()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def fetch_all(sql: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, conn=None) -> List[Dict[str, Any]]:
    if conn is not None:
        return list(conn.execute(sql, params).fetchall())
    with transaction() as tx:
        return list(tx.execute(sql, params).fetchall())


def fetch_one(sql: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, conn=None) -> Optional[Dict[str, Any]]:
    if conn is not None:
        return conn.execute(sql, params).fetchone()
    with transaction() as tx:
        return tx.execute(sql, params).fetchone()


def execute(sql: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, conn=None) -> int:
    if conn is not None:
        return conn.execute(sql, params).rowcount
    with transaction() as tx:
        return tx.execute(sql, params).rowcount


def vector_literal(values: Optional[Sequence[float]]) -> Optional[str]:
    """Format an embedding for a `%s::vector` parameter."""
    if values is None:
        return None
    return '[' + ','.join(f'{float(v):.7g}' for v in values) + ']'


def apply_schema() -> None:
    import psycopg

    url = database_url()
    if not url:
        raise RuntimeError('DATABASE_URL is not configured.')
    sql = SCHEMA_PATH.read_text(encoding='utf-8')
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute(sql)
