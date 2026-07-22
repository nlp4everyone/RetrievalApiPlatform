from .settings import settings

# Langfuse tracing - consumed via OpenTelemetry OTLP export, see app.core.tracing
LANGFUSE_PUBLIC_KEY = settings.LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY = settings.LANGFUSE_SECRET_KEY
LANGFUSE_BASE_URL = settings.LANGFUSE_BASE_URL