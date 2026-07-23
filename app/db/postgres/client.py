from typing import Any

import asyncpg
# Import schema and index
from .schema.database_schema import *
from .schema.database_indexes import *

class PostgresClient:
    """
    PostgreSQL client class for managing database connections and operations.

    This client provides an asynchronous interface to PostgreSQL using asyncpg,
    handling connection pooling, table creation, and basic database operations
    for the ChatEngine application.

    Attributes:
        _pool (asyncpg.Pool): The connection pool for database operations.
    """
    def __init__(self,
                 user :str,
                 password :str,
                 database :str,
                 host :str,
                 port :int = 5432,
                 min_size :int = 5,
                 max_size :int = 10) -> None:
        """
        Initialize PostgreSQL client with connection parameters.

        Args:
            user (str): Database username.
            password (str): Database password.
            database (str): Database name.
            host (str): Database host address.
            port (int): Database port (default: 5432).
            min_size (int): Minimum connection pool size (default: 5).
            max_size (int): Maximum connection pool size (default: 10).
        """
        # Config
        self.__user = user
        self.__password = password
        self.__database = database
        self.__host = host
        self.__port = port
        self.__min_connections = min_size
        self.__max_connections = max_size

        # Pool
        self._pool = None

    async def _create_pool(self,
                           **kwargs: Any) -> asyncpg.Pool:
        """
        Create and initialize the asyncpg connection pool.

        Args:
            **kwargs: Additional keyword arguments passed to asyncpg.create_pool.

        Returns:
            asyncpg.Pool: The created connection pool.
        """
        self._pool = await asyncpg.create_pool(user = self.__user,
                                               password = self.__password,
                                               database = self.__database,
                                               host = self.__host,
                                               port = self.__port,
                                               min_size = self.__min_connections,
                                               max_size = self.__max_connections,
                                               ssl = False,
                                               **kwargs)
        return self._pool


    @property
    def pool(self) -> asyncpg.Pool:
        """
        Get the connection pool.

        Returns:
            asyncpg.Pool: The connection pool instance.

        Raises:
            AttributeError: If pool has not been initialized.
        """
        return self._pool

    async def close(self) -> None:
        """
        Close the connection pool and clean up resources.

        Safely closes all connections in the pool if it exists.
        """
        if self._pool:
            await self._pool.close()

    async def _create_table(self) -> None:
        """
        Create all necessary database tables and indexes.

        Creates the assistant, thread, message, and run tables along with
        their respective indexes in a single transaction.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Create vector store table
                await conn.execute(CREATE_VECTOR_STORE_TABLE)
                # Create file table
                await conn.execute(CREATE_FILE_TABLE)
                # Create indexes
                await conn.execute(CREATE_VECTOR_STORE_INDEXES)
                await conn.execute(CREATE_FILE_INDEXES)
