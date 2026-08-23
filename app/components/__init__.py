"""
Swappable backends for the individual capabilities ingestion is built from.

Every package here follows the same shape: a ``base.py`` declaring the
interface, a ``provider/`` holding the implementations, and a facade service
that picks one. Nothing here orchestrates anything or knows about HTTP - see
app.pipelines and app.services for that.

Most of them pick from config at startup:

    embedding  EMBEDDING_PROVIDER         openai  | tei
               SPARSE_EMBEDDING_PROVIDER  vllm  (opt-in, SPARSE_EMBEDDING_ENABLED)
    parsing    PDF_PARSER_PROVIDER        llamaparse

Chunking is the exception: it has no setting. The splitter comes from the
request's ``chunking_splitter``, or is detected from the document - which
backend runs follows from the strategy, via app.components.chunking.registry.
"""
