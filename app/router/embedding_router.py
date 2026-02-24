# FastAPI components
from fastapi import APIRouter, Depends
# Schema
from app.schemas.embeddings import (EmbeddingObject,
                                    ListEmbeddingObject,
                                    EmbeddingUsage,
                                    EmbeddingCreateRequest)
# Exceptions
from app.exceptions.embeddings import EmbeddingModelNotFoundException
from app.startup import get_embed_model
# Config
from app.core.config import *
# Security
from app.security.auth import verify_api_key
# Token counter
from app.utils.token_counter import approximate_count_tokens
from app.utils.encoding.transform import floats_to_base64
# Logger
from loggers import SystemLogger

# Define router
embedding_router = APIRouter()

@embedding_router.post("/embeddings", response_model=ListEmbeddingObject)
async def create_embedding(request: EmbeddingCreateRequest,
                           api_key: str = Depends(verify_api_key)) -> ListEmbeddingObject:
    """
    ## Creates an embedding vector representing the input text.

    ### Args:
    - `model`: ID of the model to use
    - `input`: Input text to embed, encoded as a string or array of tokens
    - `user`: A unique identifier representing your end-user, which can help OpenAI to monitor and detect abuse.
    - `encoding_format`: The format to return the embeddings in. Can be either float or base64.


    Reference: [OpenAI Create Embedding API](https://developers.openai.com/api/reference/resources/embeddings/methods/create)

    """
    # Get embedding model
    embed_model = get_embed_model()
    # Check if requested model matches the configured model
    if request.model != EMBEDDING_MODEL_NAME:
        # Raise exception when model doesn't match
        raise EmbeddingModelNotFoundException(model_name = request.model)

    # Normalize input to list format
    inputs = [request.input] if isinstance(request.input,str) else request.input
    # Generate embeddings for all inputs
    embeddings = await embed_model.aembed_documents(inputs)
    # Create embedding objects based on encoding format
    if request.encoding_format == "float":
        # Return embeddings as float arrays
        embedding_objects = [EmbeddingObject(embedding = embedding,
                                             index = index) for (index,embedding) in enumerate(embeddings)]
    else:
        # Return embeddings as base64 encoded strings
        embedding_objects = [EmbeddingObject(embedding = floats_to_base64(embedding),
                                             index = index) for (index, embedding) in enumerate(embeddings)]

    # Estimate token count for usage tracking
    prompt_tokens = approximate_count_tokens(" ".join(inputs))
    # Return response with embeddings and usage information
    return ListEmbeddingObject(data = embedding_objects,
                               model = EMBEDDING_MODEL_NAME,
                               usage = EmbeddingUsage(prompt_tokens = prompt_tokens,
                                                      total_tokens = prompt_tokens))


