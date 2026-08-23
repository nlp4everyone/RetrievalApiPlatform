"""One table mapping each chunking strategy to the code that implements it.

This is what replaced the CHUNKING_PROVIDER setting. Naming a library and a
splitter separately made a matrix where four of the ten combinations were
invalid, and the invalid ones only blew up in the worker - after the vector
store record already read IN_PROGRESS, leaving the store stuck there forever.
Here the mapping is a total function on the enum: every strategy is buildable,
so there is nothing left to reject.

Adding a strategy is a config model, a build function, and one line below.
"""
from dataclasses import dataclass
from typing import Callable, Dict, Type

from pydantic import BaseModel

from app.schemas.chunking import ChunkingStrategy
from app.schemas.chunking.chunking_config import (BaseChunkingConfig,
                                                  CharacterChunkingConfig,
                                                  MarkdownChunkingConfig,
                                                  RecursiveChunkingConfig,
                                                  SentenceChunkingConfig,
                                                  TokenChunkingConfig)

from .base import BaseChunkingProvider
from .provider.chonkie_provider import build_recursive, build_sentence, build_token
from .provider.langchain_provider import build_character, build_markdown


@dataclass(frozen=True)
class ChunkerSpec:
    """How to build one strategy's splitter.

    Attributes:
        config_model: The model holding that strategy's knobs, and nothing else
        build: Turns an instance of that model into a ready provider
    """
    config_model: Type[BaseModel]
    build: Callable[[BaseChunkingConfig], BaseChunkingProvider]


REGISTRY: Dict[ChunkingStrategy, ChunkerSpec] = {
    ChunkingStrategy.RECURSIVE: ChunkerSpec(RecursiveChunkingConfig, build_recursive),
    ChunkingStrategy.TOKEN:     ChunkerSpec(TokenChunkingConfig,     build_token),
    ChunkingStrategy.SENTENCE:  ChunkerSpec(SentenceChunkingConfig,  build_sentence),
    ChunkingStrategy.CHARACTER: ChunkerSpec(CharacterChunkingConfig, build_character),
    ChunkingStrategy.MARKDOWN:  ChunkerSpec(MarkdownChunkingConfig,  build_markdown),
}

# The safety net that used to be a config validator: adding an enum value and
# forgetting its entry here now fails at import - in every process, at startup -
# instead of raising KeyError inside the worker on the first request that uses it
_missing = set(ChunkingStrategy) - set(REGISTRY)
if _missing:
    raise RuntimeError(f"No chunker registered for strategy: "
                       f"{sorted(strategy.value for strategy in _missing)}")
