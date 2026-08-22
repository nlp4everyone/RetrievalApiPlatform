"""Liveness probe for the TaskIQ worker container.

The worker has no HTTP surface, so "is the process up?" is all Docker can see
on its own - and a worker that has lost Postgres or Redis stays up while doing
nothing. This checks the two connections whose loss makes it useless: Redis,
which is where tasks arrive from, and Postgres, which every ingestion job
writes its outcome to.

Exits 0 when both answer, 1 otherwise.
"""
import asyncio
import sys

import asyncpg
from redis.asyncio import Redis

from app.core.config import (POSTGRES_DB,
                             POSTGRES_HOST,
                             POSTGRES_PASSWORD,
                             POSTGRES_USER,
                             REDIS_URL)

# Not settings.POSTGRES_PORT - that's the host port compose maps (.env),
# while inside the network postgres always listens on its own 5432. Same
# convention as app.startup and app/core/config/database.py.
POSTGRES_PORT = 5432

# Below Docker's own `timeout`, so a hung dependency fails the check here with a
# named reason instead of being killed anonymously by the daemon
CHECK_TIMEOUT_S = 5.0


async def _check_postgres() -> None:
    """Open one connection and run the cheapest possible query.

    Raises:
        Exception: If the database is unreachable or rejects the connection
    """
    connection = await asyncpg.connect(user = POSTGRES_USER,
                                       password = POSTGRES_PASSWORD,
                                       database = POSTGRES_DB,
                                       host = POSTGRES_HOST,
                                       port = POSTGRES_PORT,
                                       timeout = CHECK_TIMEOUT_S)
    try:
        await connection.fetchval("SELECT 1")
    finally:
        await connection.close()


async def _check_redis() -> None:
    """Ping the broker Redis.

    Raises:
        Exception: If Redis is unreachable or does not answer the ping
    """
    client = Redis.from_url(REDIS_URL, socket_connect_timeout = CHECK_TIMEOUT_S)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _main() -> int:
    """
    Returns:
        int: 0 when both dependencies answered, 1 when either did not
    """
    for name, check in (("redis", _check_redis), ("postgres", _check_postgres)):
        try:
            await asyncio.wait_for(check(), timeout = CHECK_TIMEOUT_S)
        except Exception as error:
            print(f"worker healthcheck: {name} unreachable: {error!r}", file = sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
