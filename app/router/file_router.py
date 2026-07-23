# FastAPI dependencies
from fastapi import UploadFile, Form, APIRouter, Depends, File
# Typing
from typing import Optional
# Schema
from app.schemas.file import (FilePurposes,
                              FileObject,
                              FileListResponse,
                              FileQueryRequest,
                              FileDeletedResponse)
# Service
from app.services.file import FileService
# Security
from app.security.auth import verify_api_key
# Dependencies
from app.dependencies.file_validation import validate_file

# Router
file_router = APIRouter()

@file_router.post("/files", response_model = FileObject)
async def upload_file(purpose: FilePurposes = Form(..., description="The intended purpose of the uploaded file. Must be one of: assistants, batch, fine-tune, vision, user_data, evals"),
                      file: UploadFile = Depends(validate_file),
                      expires_after_anchor: Optional[str] = Form(None, alias="expires_after[anchor]", description="Anchor point for expiration time calculation"),
                      expires_after_seconds: Optional[int] = Form(None, alias="expires_after[seconds]", description="Number of seconds after which the file will expire"),
                      api_key: str = Depends(verify_api_key)) -> FileObject:
    """
    ## Upload a file that can be used across various endpoints.

    ### Args:
    - `purpose`: The intended use case for the uploaded file.
    - `file`: The file to be uploaded.
    - `expires_after_anchor`: Optional anchor point for expiration time calculation.
    - `expires_after_seconds`: Optional duration in seconds after which the file will expire.

    Reference: [OpenAI Create File API](https://developers.openai.com/api/reference/resources/files/methods/create)

    """
    return await FileService.upload_file(file, api_key, purpose, expires_after_seconds)

@file_router.get("/files", response_model = FileListResponse)
async def list_files(query: FileQueryRequest = Depends(),
                     api_key: str = Depends(verify_api_key)) -> FileListResponse:
    """
    ## Returns a list of files.

    ### Args:
    - `purpose`: Only return files with the given purpose.
    - `after`: A cursor for use in pagination. after is an object ID that defines your place in the list
    - `limit`: A limit on the number of objects to be returned. Limit can range between 1 and 10,000, and the default is 10,000.
    - `order`: Sort order by the created_at timestamp of the objects. asc for ascending order and desc for descending order.

    Reference: [OpenAI List Files API](https://developers.openai.com/api/reference/resources/files/methods/list)

    """
    return await FileService.list_files(api_key, query)

@file_router.get("/files/{file_id}", response_model = FileObject)
async def get_file_by_id(file_id: str,
                         api_key: str = Depends(verify_api_key)) -> FileObject:
    """
    ## Returns information about a specific file.

    ### Args:
    - `file_id`: The unique identifier of the file to retrieve.

    Reference: [OpenAI Retrieve File API](https://developers.openai.com/api/reference/resources/files/methods/retrieve)

    """
    return await FileService.get_file_by_id(file_id)

@file_router.delete("/files/{file_id}", response_model = FileDeletedResponse)
async def delete_file(file_id: str,
                      api_key: str = Depends(verify_api_key)) -> FileDeletedResponse:
    """
    ## Delete a file and remove it from all vector stores.

    ### Args:
    - `file_id`: The unique identifier of the file to delete.

    Reference: [OpenAI Delete File API](https://developers.openai.com/api/reference/resources/files/methods/delete)

    """
    result = await FileService.delete_file(file_id, api_key)
    return FileDeletedResponse(id=result["id"])
