from fastapi import HTTPException, status
from app.schemas.vector_store.requests import *
# Typing
from typing import Union
# Helper
from app.utils.key_generator import generate_vectorstore_id
# Qdrant component
from qdrant_client import models

def _convert_to_qdrant_filter(filter_obj: Union[ComparisonFilter, CompoundFilter]) -> models.Filter:
    """
    Convert the filter object to Qdrant's Filter model.

    Args:
        filter_obj: The filter object to convert (ComparisonFilter or CompoundFilter)

    Returns:
        models.Filter: The Qdrant filter object
    """
    if isinstance(filter_obj, ComparisonFilter):
        # Handle comparison filter
        if filter_obj.type == "eq":
            return models.FieldCondition(key=filter_obj.key, match=models.MatchValue(value=filter_obj.value))
        elif filter_obj.type == "ne":
            return models.FieldCondition(key=filter_obj.key, match=models.MatchExcept(except_=filter_obj.value))
        elif filter_obj.type == "gt":
            return models.FieldCondition(key=filter_obj.key, range=models.Range(gt=filter_obj.value))
        elif filter_obj.type == "gte":
            return models.FieldCondition(key=filter_obj.key, range=models.Range(gte=filter_obj.value))
        elif filter_obj.type == "lt":
            return models.FieldCondition(key=filter_obj.key, range=models.Range(lt=filter_obj.value))
        elif filter_obj.type == "lte":
            return models.FieldCondition(key=filter_obj.key, range=models.Range(lte=filter_obj.value))
        elif filter_obj.type == "in":
            return models.FieldCondition(key=filter_obj.key, match=models.MatchAny(any_=filter_obj.value))
        elif filter_obj.type == "nin":
            return models.FieldCondition(key=filter_obj.key, match=models.MatchExcept(except_=filter_obj.value))
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported comparison type: {filter_obj.type}"
            )
    elif isinstance(filter_obj, CompoundFilter):
        # Handle compound filter (AND/OR)
        conditions = [_convert_to_qdrant_filter(f) for f in filter_obj.filters]
        if filter_obj.type == "and":
            return models.Filter(must=conditions)
        elif filter_obj.type == "or":
            return models.Filter(should=conditions)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported compound filter type: {filter_obj.type}"
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported filter type: {type(filter_obj).__name__}"
        )