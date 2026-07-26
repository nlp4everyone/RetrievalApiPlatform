# Inherit
from ...base import BaseParsingProvider
# Other component
import asyncio

class TextProvider(BaseParsingProvider):
    """Decode plain-text formats (.txt, .md) in process."""

    def __init__(self, encoding: str = "utf-8") -> None:
        """
        Configure the decoder.

        Args:
            encoding (str): Character encoding to decode with (default: utf-8)
        """
        self._encoding = encoding

    async def parse(self, file_bytes: bytes) -> str:
        """
        Decode bytes to text, ignoring anything the encoding cannot represent.

        Runs on a worker thread: decoding a large file is CPU-bound and would
        otherwise stall the event loop for every other task in this process.

        Args:
            file_bytes (bytes): Raw contents of the file

        Returns:
            str: Decoded text
        """
        return await asyncio.to_thread(file_bytes.decode, self._encoding, "ignore")