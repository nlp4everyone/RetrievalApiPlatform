from contextlib import asynccontextmanager
# Other components
import tempfile, aiofiles, os

@asynccontextmanager
async def async_temp_file(file_bytes: bytes,
                          *,
                          suffix: str = ""):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    try:
        async with aiofiles.open(path, "wb") as f:
            await f.write(file_bytes)
        yield path
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
