from fastapi import APIRouter, Depends
# Schemas
from app.schemas.vector_store.requests import *
from app.schemas.vector_store.responses import *
# Security
from app.security.auth import verify_api_key
# Service
from app.services.vector_store import VectorStoreService

# Define router
vector_store_router = APIRouter()

@vector_store_router.post("/vector_stores", response_model=VectorStoreObject)
async def create_vector_store(request: VectorStoreCreateRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreObject:
    """
    ## Create a vector store.

    ### Args
    - `name` (str): The name of the vector store.
    - `description` (str, optional): A description for the vector store. Can be used to describe the vector store's purpose.
    - `file_ids` (List[str], optional): A list of File IDs that the vector store should use. Useful for tools like file_search that can access files.
    - `expires_after` (optional): The expiration policy for a vector store.
    - `metadata` (optional): Set of 16 key-value pairs that can be attached to an object
    - `chunking_strategy` (optional): The chunking strategy used to chunk the file(s).

    Reference: [OpenAI Create Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/create)

    """
    return await VectorStoreService.create(request, api_key)

@vector_store_router.get("/vector_stores", response_model=ListVectorStoreObject)
async def list_vector_stores(query_object: VectorStoreQueryRequest = Depends(),
                             api_key: str = Depends(verify_api_key)) -> ListVectorStoreObject:
    """
    ## Returns a list of vector stores.

    ### Args:
    - `limit` (int, optional): A limit on the number of objects to be returned. Limit can range between 1 and 100, and the default is 20
    - `order` (str, optional): Sort order by the created_at timestamp of the objects. asc for ascending order and desc for descending order.
    - `after` (str, optional): A cursor for use in pagination. after is an object ID that defines your place in the lis
    - `before` (str, optional): A cursor for use in pagination. before is an object ID that defines your place in the list.

    Reference: [OpenAI List Vector Stores API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/list)

    """
    return await VectorStoreService.list(api_key, query_object)


@vector_store_router.get("/vector_stores/{vector_store_id}", response_model=VectorStoreObject)
async def get_vector_store(vector_store_id: str,
                           api_key: str = Depends(verify_api_key)) -> VectorStoreObject:
    """
    ## Retrieves a vector store.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store

    Reference: [OpenAI Retrieve Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/retrieve)

    """
    return await VectorStoreService.get(vector_store_id, api_key)


@vector_store_router.post("/vector_stores/{vector_store_id}", response_model=VectorStoreObject)
async def modify_vector_store(vector_store_id: str,
                              request: VectorStoreModifyRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreObject:
    """
    ## Modifies a vector store.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store to update
    - `name` (str, optional): The name of the vector store.
    - `metadata` (Dict[str, Any], optional): Set of 16 key-value pairs that can be attached to an object.

    Reference: [OpenAI Update Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/update)

    """
    return await VectorStoreService.modify(vector_store_id, request, api_key)


@vector_store_router.delete("/vector_stores/{vector_store_id}", response_model=VectorStoreDeletion)
async def delete_vector_store(vector_store_id: str,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreDeletion:
    """
    ## Delete a vector store.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store to delete

    Reference: [OpenAI Delete Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/delete)

    """
    return await VectorStoreService.delete(vector_store_id, api_key)

@vector_store_router.post("/vector_stores/{vector_store_id}/search", response_model=VectorStoreSearchResponse)
async def search_vector_store(vector_store_id: str,
                              search_request: VectorStoreSearchRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreSearchResponse:
    """
    ## Search a vector store for relevant chunks based on a query and file attributes filter.

    ### Args
    - `vector_store_id` (str): The ID of the vector store to search in.
    - `query` (Union[str, List[str]]): A query string for a search
    - `filters` (Optional[Union[ComparisonFilter, CompoundFilter]]): A filter to apply based on file attributes.
    - `max_num_results` (int, optional): The maximum number of results to return. This number should be between 1 and 50 inclusive.
    - `ranking_options` (Optional[RankingOptions]): Ranking options for search.
    
    ### Returns
    - `VectorStoreSearchResponse`: A response containing the search results.

    Reference: [OpenAI Search Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)

    """
    return await VectorStoreService.search(vector_store_id, search_request, api_key)
