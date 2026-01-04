from abc import ABC, abstractmethod
from typing import Union

class BaseTextParser(ABC):
    """Parse file bytes into raw text"""

    @abstractmethod
    def parse(self, file_input: Union[bytes,str]) -> str:
        pass