"""
Swappable backends for the individual capabilities ingestion is built from.

Every package here follows the same shape: a ``base.py`` declaring the
interface, a ``provider/`` holding the implementations, and a facade service
whose ``from_settings()`` picks one from config. Nothing here orchestrates
anything or knows about HTTP - see app.pipelines and app.services for that.

    chunking   CHUNKING_PROVIDER      chonkie | langchain
    embedding  EMBEDDING_PROVIDER     openai  | tei
    parsing    PDF_PARSER_PROVIDER    llamaparse
"""