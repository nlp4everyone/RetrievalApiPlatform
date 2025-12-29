import asyncpg

class PostgresService:
    def __init__(self,
                 user :str,
                 password :str,
                 database :str,
                 host :str,
                 port :int = 5432,
                 min_size :int = 5,
                 max_size :int = 10,
                 **kwargs):
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

    async def _create_pool(self) -> asyncpg.Pool:
        self._pool = await asyncpg.create_pool(user = self.__user,
                                               password = self.__password,
                                               database = self.__database,
                                               host = self.__host,
                                               port = self.__port,
                                               min_size = self.__min_connections,
                                               max_size = self.__max_connections,
                                               ssl = False)
        return self._pool


    @property
    def pool(self) -> asyncpg.Pool:
        """Init pool"""
        return self._pool

    async def close(self):
        """Close pool"""
        if self._pool:
            await self._pool.close()