import base64
import numpy as np
from typing import List

def floats_to_base64(data :List[float]):
    """
    Convert list[float] or list[list[float]] to Base64 with minimal overhead.
    Uses numpy for efficient packing (float32).
    """
    arr = np.asarray(data, dtype=np.float32)
    return base64.b64encode(arr.tobytes()).decode('utf-8')