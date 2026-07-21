from typing import Optional
from datetime import datetime


def convert_to_unix_timestamp(dt: Optional[datetime]) -> Optional[int]:
    """
    Convert datetime object to Unix timestamp (seconds since epoch).
    
    Args:
        dt: Datetime object to convert, or None
        
    Returns:
        Unix timestamp as integer, or None if input is None
    """
    return int(dt.timestamp()) if dt else None
