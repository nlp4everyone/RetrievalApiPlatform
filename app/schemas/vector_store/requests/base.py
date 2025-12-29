# Dependencies
from typing import Literal, Union
from pydantic import BaseModel, Field

class AutoChunkingStrategy(BaseModel):
    """
    The default chunking strategy.
    Uses max_chunk_size_tokens=800 and chunk_overlap_tokens=400.
    """
    type: Literal["auto"] = Field(default="auto",
                                  description="Always 'auto'. Uses the default automatic chunking strategy.")


class StaticChunkingConfig(BaseModel):
    max_chunk_size_tokens: int = Field(...,
                                       gt=0,
                                       description="Maximum number of tokens per chunk.")
    chunk_overlap_tokens: int = Field(...,
                                      ge=0,
                                      description="Number of overlapping tokens between adjacent chunks.")


class StaticChunkingStrategy(BaseModel):
    type: Literal["static"] = Field(...,
                                    description="Always 'static'. Enables custom chunk size and overlap.")
    static: StaticChunkingConfig = Field(...,
                                         description="Configuration for static chunking strategy.")


class ExpiresAfter(BaseModel):
    anchor: Literal["last_active_at"] = Field(...,
                                              description="Anchor timestamp after which the expiration policy applies. "
                                                          "Currently supported value: 'last_active_at'.")
    days: int = Field(...,
                      gt=0,
                      description="Number of days after the anchor time that the vector store will expire.")


ChunkingStrategy = Union[AutoChunkingStrategy, StaticChunkingStrategy]
