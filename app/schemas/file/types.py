from enum import Enum
from strenum import StrEnum

class FileFormat(Enum):
    TEXT = 0
    DOCX = 1
    PDF = 2

class UploadingStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"