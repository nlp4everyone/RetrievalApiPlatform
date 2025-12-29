# FastAPI components
from fastapi import APIRouter, Depends
# Schema
from app.schemas.embeddings import (EmbeddingObject,
                                    ListEmbeddingObject,
                                    EmbeddingUsage,
                                    EmbeddingCreateRequest)
# Exceptions
from app.exceptions.embeddings import EmbeddingModelNotFoundException
#  Get model
from app.startup import get_embed_model
# Config
from app.core.config.constants import EMBEDDING_MODEL_NAME
# Security
from app.security.auth import verify_api_key
# Token counter
from app.utils.token_counter import approximate_count_tokens
from app.utils.transform import floats_to_base64
# Logger
from loggers import SystemLogger

# Define router
embedding_router = APIRouter()

@embedding_router.post("/embeddings", response_model=ListEmbeddingObject)
async def create_embedding(request: EmbeddingCreateRequest,
                           api_key: str = Depends(verify_api_key)) -> ListEmbeddingObject:
    """
    ## Generate embeddings for the input text(s).

    This endpoint takes a text or list of texts and returns their corresponding vector embeddings
    using the configured embedding model. It supports both string and list of strings as input.

    ### Args:
    - `model`: The name of the embedding model to use
    - `input`: Text or list of texts to generate embeddings for
    - `encoding_format`: The format of the returned embeddings ('float' or 'base64')

    ### Example
        ```json
        {
            "model": "text-embedding-ada-002",
            "input": "Sample text to embed",
            "encoding_format": "float"
        }
        ```
    """
    # Embed model
    embed_model = get_embed_model()
    # Check model name
    if request.model != EMBEDDING_MODEL_NAME:
        # When not correct, raise exception
        raise EmbeddingModelNotFoundException(model_name = request.model)

    # Normalize input
    inputs = [request.input] if isinstance(request.input,str) else request.input
    # Embed object
    embeddings = await embed_model.aembed_documents(inputs)
    # Get embedding objects
    if request.encoding_format == "float":
        # Float type
        embedding_objects = [EmbeddingObject(embedding = embedding,
                                             index = index) for (index,embedding) in enumerate(embeddings)]
    else:
        # Base64 type
        embedding_objects = [EmbeddingObject(embedding = floats_to_base64(embedding),
                                             index = index) for (index, embedding) in enumerate(embeddings)]

    # Estimate number of tokens
    prompt_tokens = approximate_count_tokens(" ".join(inputs))
    # Return
    return ListEmbeddingObject(data = embedding_objects,
                               model = EMBEDDING_MODEL_NAME,
                               usage = EmbeddingUsage(prompt_tokens = prompt_tokens,
                                                      total_tokens = prompt_tokens))


