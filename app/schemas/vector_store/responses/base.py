# Typing
from pydantic import Field, BaseModel
from typing import Literal

class VectorStoreDeletion(BaseModel):
    """Represents the response when a vector store is deleted."""
    id: str = Field(...,
                    description="The ID of the deleted vector store")
    object: Literal["vector_store.deleted"] = Field("vector_store.deleted",
                                                    description="The object type, which is always vector_store.deleted")
    deleted: bool = Field(...,
                          description="True if the vector store was deleted")


class VectorStoreFileCounts(BaseModel):
    """Represents the counts of files in different statuses within a vector store."""
    cancelled: int = Field(0,
                           description="The number of files that were cancelled.")
    completed: int = Field(0,
                           description="The number of files that have been successfully processed.")
    failed: int = Field(0,
                        description="The number of files that have failed to process.")
    in_progress: int = Field(0,
                             description="The number of files that are currently being processed.")
    total: int = Field(0,
                       description="The total number of files.")


class VectorStoreExpiresAfter(BaseModel):
    """Represents the expiration policy for a vector store."""
    anchor: Literal["last_active_at"] = Field(...,
                                              description="Anchor timestamp after which the expiration policy applies. "
                                                          "Supported anchors: last_active_at.")
    days: int = Field(...,
                      description="The number of days after the anchor time that the vector store will expire.")