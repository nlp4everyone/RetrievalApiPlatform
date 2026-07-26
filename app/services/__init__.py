"""
Application services backing the HTTP API - one package per resource.

These sit directly under app.api.router: they take the api_key, validate ownership,
talk to app.db, raise the HTTP exceptions the handlers surface, and return the
API response schemas. Anything reusable below that line belongs in
app.components (swappable backends) or app.pipelines (orchestration).

    file          /v1/files
    vector_store  /v1/vector_stores
"""