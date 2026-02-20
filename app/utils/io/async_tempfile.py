from contextlib import asynccontextmanager
# Other components
import tempfile, aiofiles, os

@asynccontextmanager
async def async_temp_file(file_bytes: bytes,
                          *,
                          suffix: str = ""):
    """
    Create an async context manager for temporary file handling.
    
    This function creates a temporary file from bytes, writes it asynchronously,
    yields the file path for use, and automatically cleans up the file afterward.
    
    Args:
        file_bytes: The bytes content to write to the temporary file
        suffix: Optional file suffix/extension (e.g., '.txt', '.json')
        
    Yields:
        str: The path to the created temporary file
        
    Example:
        async with async_temp_file(b"hello world", suffix=".txt") as temp_path:
            # Use temp_path here
            with open(temp_path, 'r') as f:
                content = f.read()
        # File is automatically deleted after context exits
        
    Note:
        The temporary file is automatically deleted when the context exits,
        even if an exception occurs within the context.
    """
    # Create temporary file with optional suffix
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)  # Close file descriptor, we'll use aiofiles for async writing

    try:
        # Write bytes asynchronously to the temporary file
        async with aiofiles.open(path, "wb") as f:
            await f.write(file_bytes)
        yield path
    finally:
        # Ensure cleanup happens even if an exception occurs
        try:
            os.remove(path)
        except FileNotFoundError:
            # File was already deleted or never created properly
            pass
