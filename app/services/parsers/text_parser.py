from .base import BaseTextParser
from concurrent.futures import ThreadPoolExecutor
import asyncio

class TextParser(BaseTextParser):
    def __init__(self, encoding: str = "utf-8"):
        self.encoding = encoding

    def parse(self, file_bytes: bytes) -> str:
        return file_bytes.decode(self.encoding, errors="ignore")


class AsyncTextParser:
    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        # Parser
        self._parser = TextParser()

    async def parse_file(self,
                         file_bytes: bytes,) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor,
                                          self._parser.parse,
                                          file_bytes)