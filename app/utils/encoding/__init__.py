"""
Encoding and data transformation utilities.

This module provides utilities for transforming data between different formats,
particularly for converting numerical data to encoded formats for transmission
or storage.
"""

from .transform import floats_to_base64

__all__ = ["floats_to_base64"]
