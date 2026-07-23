from typing import Any

from minio import Minio
from loggers import SystemLogger

class MinioService:
    def __init__(self,
                 endpoint_url :str,
                 access_key :str,
                 secret_key :str,
                 secure :bool = False,
                 **kwargs: Any) -> None:
        # Config
        self.__endpoint_url = endpoint_url
        self.__access_key = access_key
        self.__secret_key = secret_key
        self.__secure = secure

        # Create client
        self._client = Minio(endpoint = self.__endpoint_url,
                             access_key = self.__access_key,
                             secret_key = self.__secret_key,
                             secure = self.__secure)
        # Try connect
        try:
            # Check list buckets
            self._client.list_buckets()
        except Exception as e:
            SystemLogger.error(f"Failed to connect to Minio: {e!r}")

    @property
    def client(self) -> Minio:
        return self._client