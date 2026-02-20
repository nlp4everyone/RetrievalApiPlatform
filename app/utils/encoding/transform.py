import base64
import numpy as np
from typing import List


def floats_to_base64(data: List[float]) -> str:
    """
    Convert list of floats to Base64 encoded string.
    
    This function efficiently converts a list of float values (or nested lists)
    to a Base64 encoded string using numpy for optimal packing. The conversion
    uses float32 precision to balance accuracy and storage efficiency.
    
    Args:
        data: List of float values or list of lists of floats to be encoded
        
    Returns:
        str: Base64 encoded string representation of the float data
    """
    # Convert input to numpy array with float32 dtype for efficiency
    arr = np.asarray(data, dtype=np.float32)
    
    # Convert numpy array to bytes, then encode to Base64 string
    return base64.b64encode(arr.tobytes()).decode('utf-8')