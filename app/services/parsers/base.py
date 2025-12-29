from abc import ABC, abstractmethod

class BaseTextParser(ABC):
    """Parse file bytes into raw text"""

    @abstractmethod
    def parse(self, file_bytes: bytes) -> str:
        pass