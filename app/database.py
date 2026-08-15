import threading
from contextlib import contextmanager

import psycopg2
from psycopg2.pool import PoolError, ThreadedConnectionPool

from app.config import get_settings


_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()
_pool_slots: threading.BoundedSemaphore | None = None


def _connection_kwargs() -> dict:
    settings = get_settings()
    return {
        "dsn": settings.database_url,
        "sslmode": "require",
        "connect_timeout": 5,
        "options": (
            "-c timezone=America/Argentina/Buenos_Aires "
            "-c statement_timeout=30000 "
            "-c idle_in_transaction_session_timeout=15000 "
            "-c search_path=pharmacy,public"
        ),
        "keepalives": 1,
        "keepalives_idle": 10,
        "keepalives_interval": 5,
        "keepalives_count": 3,
    }


def _get_pool() -> ThreadedConnectionPool:
    global _pool, _pool_slots
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            settings = get_settings()
            _pool = ThreadedConnectionPool(
                settings.db_pool_min_connections,
                settings.db_pool_max_connections,
                **_connection_kwargs(),
            )
            _pool_slots = threading.BoundedSemaphore(settings.db_pool_max_connections)
    return _pool


@contextmanager
def connection():
    settings = get_settings()
    pool = _get_pool()
    slots = _pool_slots
    if slots is None or not slots.acquire(timeout=settings.db_pool_acquire_timeout_seconds):
        raise PoolError("database connection pool exhausted")
    conn = None
    try:
        conn = pool.getconn()
        if conn.closed:
            raise psycopg2.OperationalError("pooled database connection is closed")
        conn.rollback()
        yield conn
    finally:
        if conn is not None:
            close = bool(conn.closed)
            if not close:
                try:
                    conn.rollback()
                except psycopg2.Error:
                    close = True
            pool.putconn(conn, close=close)
        slots.release()


def close_pool() -> None:
    global _pool, _pool_slots
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
        _pool = None
        _pool_slots = None

