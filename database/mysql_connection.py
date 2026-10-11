"""选课系统的 MySQL 连接池与事务辅助工具。

连接配置从 MYSQL_* 环境变量读取。导入本模块时不会立即连接数据库；
首次获取连接时才会创建连接池。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator


_pool: Any | None = None
_pool_lock = Lock()
_POOL_NAME = "course_registration_pool"


def _pool_options() -> tuple[dict[str, Any], int]:
    password = os.environ.get("MYSQL_PASSWORD")
    if not password:
        raise RuntimeError("请先在当前终端设置 MYSQL_PASSWORD。")

    try:
        port = int(os.environ.get("MYSQL_PORT", "3306"))
        pool_size = int(os.environ.get("MYSQL_POOL_SIZE", "10"))
        timeout = int(os.environ.get("MYSQL_CONNECT_TIMEOUT_SECONDS", "5"))
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT、MYSQL_POOL_SIZE 和 MYSQL_CONNECT_TIMEOUT_SECONDS 必须是整数。") from exc

    if not 1 <= port <= 65535:
        raise RuntimeError("MYSQL_PORT 必须在 1 到 65535 之间。")
    if not 1 <= pool_size <= 32:
        raise RuntimeError("MYSQL_POOL_SIZE 必须在 1 到 32 之间。")
    if timeout < 1:
        raise RuntimeError("MYSQL_CONNECT_TIMEOUT_SECONDS 必须大于 0。")

    options = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
        "port": port,
        "user": os.environ.get("MYSQL_USER", "course_app"),
        "password": password,
        "database": os.environ.get("MYSQL_DATABASE", "course_registration"),
        "charset": "utf8mb4",
        "collation": "utf8mb4_0900_ai_ci",
        "connection_timeout": timeout,
        "autocommit": False,
    }
    return options, pool_size


def _get_pool() -> Any:
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            from mysql.connector.pooling import MySQLConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "缺少 MySQL 驱动；请运行 `py -3 -m pip install -r requirements-mysql.txt`。"
            ) from exc

        options, pool_size = _pool_options()
        _pool = MySQLConnectionPool(
            pool_name=_POOL_NAME,
            pool_size=pool_size,
            pool_reset_session=True,
            **options,
        )
        return _pool


def get_connection() -> Any:
    """获取一个连接池中的连接；调用 ``close()`` 可将连接归还连接池。"""
    return _get_pool().get_connection()


@contextmanager
def transaction() -> Iterator[Any]:
    """提供一个事务连接；操作成功时提交，发生异常时回滚。"""
    connection = get_connection()
    try:
        connection.start_transaction()
        yield connection
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except Exception:
            # 保留原始操作异常；关闭连接会将其归还连接池，连接池会在复用前重置会话。
            pass
        raise
    finally:
        connection.close()
