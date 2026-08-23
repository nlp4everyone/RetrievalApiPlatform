# Typing
from typing import Dict, List, Optional
from pydantic import Field, BaseModel, model_validator
# Dependencies
from .base import ChunkingStrategy, ExpiresAfter
# Careful with the name clash: `.base.ChunkingStrategy` is the OpenAI
# Union[Auto, Static] sizing object, while `app.schemas.chunking.
# ChunkingStrategy` is the five-value splitter enum. Aliased so the two never
# read as the same thing.
from app.schemas.chunking import ChunkingStrategy as ChunkingSplitter

class VectorStoreCreateRequest(BaseModel):
    """Request model for creating a new vector store."""
    name: Optional[str] = Field(default=None,
                                description="The name of the vector store.")
    description: Optional[str] = Field(default=None,
                                       description="A description for the vector store. "
                                                   "Can be used to describe the vector store's purpose.")
    chunking_strategy: Optional[ChunkingStrategy] = Field(default=None,
                                                          description=("The chunking strategy used to chunk the file(s). "
                                                                       "If not set, the auto strategy is used. "
                                                                       "Only applicable if file_ids is non-empty."))
    chunking_splitter: Optional[ChunkingSplitter] = Field(default=None,
                                                          description=("Which algorithm splits the text: recursive, markdown, "
                                                                       "character, token or sentence. Left unset, the splitter "
                                                                       "is detected from the document. Orthogonal to "
                                                                       "chunking_strategy, which only decides chunk size. "
                                                                       "Not part of the OpenAI API - send it via extra_body."))
    expires_after: Optional[ExpiresAfter] = Field(default=None,
                                                  description="The expiration policy for the vector store.")
    file_ids: Optional[List[str]] = Field(default=None,
                                          description="A list of File IDs that the vector store should use. "
                                                      "Useful for tools like file_search that can access files.")
    metadata: Optional[Dict[str, str]] = Field(default=None,
                                               description=("Set of up to 16 key-value pairs attached to the object. "
                                                            "Keys: max 64 characters. Values: max 512 characters."))

    @model_validator(mode="after")
    def validate_overlap_supported(self) -> "VectorStoreCreateRequest":
        """Reject an overlap the explicitly chosen splitter cannot honour.

        Only checked when the caller named the splitter themselves. A request
        that leaves chunking_splitter unset keeps working exactly as before -
        clients have always been able to send static chunking with an overlap
        and have it silently ignored, and failing those now would break them.
        Asking for a splitter by name is a different act: silently dropping
        part of that request is worse than saying so.

        Raises:
            ValueError: If the named splitter has no overlap and one was asked for
        """
        splitter = self.chunking_splitter
        if splitter is None or splitter.supports_overlap:
            return self

        sizing = self.chunking_strategy
        if (sizing is not None
                and sizing.type == "static"
                and sizing.static.chunk_overlap_tokens > 0):
            raise ValueError(f"chunking_splitter {splitter.value!r} does not support overlapping "
                             f"chunks; set chunk_overlap_tokens to 0 or choose another splitter")
        return self
