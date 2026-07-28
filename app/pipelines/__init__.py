"""
Multi-step orchestration built on top of app.components and app.db.

Pipelines run outside the request/response cycle - ingestion runs in the TaskIQ
worker - so nothing here raises HTTP exceptions or returns API schemas. Each
pipeline owns its own tracing shape, emitting one Langfuse observation per step.

    ingestion   download -> parse -> chunk -> embed+index
    retrieval   embed_query -> retrieve -> fuse
"""