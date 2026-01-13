# Typing
from typing import Any, Dict, Optional
# Table
from .tables import CREATE_VECTOR_STORE_TABLE, CREATE_VECTOR_STORE_INDEXES
# Exception
from app.exceptions.vector_store import VectorStoreNotFoundException
# Other component
import asyncpg, json

class PostgresVectorStore:
    @staticmethod
    async def _create_table(pool: asyncpg.Pool) -> None:
        """Create vector_stores table and indexes if they do not exist."""
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(CREATE_VECTOR_STORE_TABLE)
                await conn.execute(CREATE_VECTOR_STORE_INDEXES)

    @staticmethod
    async def _check_vector_store_existance(pool: asyncpg.Pool,
                                            vector_store_id: str,
                                            api_key: str):
        # Define query
        query = """
        SELECT * FROM vector_stores
        WHERE id = $1 AND api_key = $2
        """

        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, vector_store_id, api_key)

        # Raise exception
        if not row: raise VectorStoreNotFoundException(vector_store_id)

    @staticmethod
    async def create(pool: asyncpg.Pool,
                     *,
                     id: str,
                     api_key: str,
                     name: Optional[str],
                     description: Optional[str],
                     created_at: Any,
                     last_active_at: Any,
                     status: str,
                     usage_bytes: int,
                     metadata: Optional[Dict[str, str]] = None,
                     expires_at: Optional[Any] = None,
                     expires_after: Optional[int] = None,
                     chunking_strategy: Optional[Dict[str, Any]] = None,
                     vector_store_type: Optional[str] = None) -> Dict[str, Any]:
        """Insert a new vector store record and return the stored row."""

        query = """
            INSERT INTO vector_stores (
                id,
                api_key,
                name,
                description,
                created_at,
                last_active_at,
                status,
                usage_bytes,
                metadata,
                expires_at,
                expires_after,
                chunking_strategy,
                vector_store_type
            ) VALUES (
                $1, $2, $3, $4, $5::timestamptz, $6::timestamptz,
                $7, $8, $9::jsonb, $10::timestamptz, $11::integer, $12::jsonb, $13
            )
            RETURNING *;
        """

        async with pool.acquire() as conn:
            row = await conn.fetchrow(query,
                                      id,
                                      api_key,
                                      name,
                                      description,
                                      created_at,
                                      last_active_at,
                                      status,
                                      usage_bytes,
                                      json.dumps(metadata) if metadata is not None else None,
                                      expires_at,
                                      expires_after,
                                      json.dumps(chunking_strategy) if chunking_strategy is not None else None,
                                      vector_store_type)

        record = dict(row)
        # Normalize JSONB fields to Python types
        for key in ("metadata", "chunking_strategy"):
            value = record.get(key)
            if isinstance(value, str):
                try:
                    record[key] = json.loads(value)
                except json.JSONDecodeError:
                    record[key] = None
        return record

    @staticmethod
    async def get(pool: asyncpg.Pool,
                  vector_store_id: str,
                  api_key: str) -> Optional[Dict[str, Any]]:
        """Get a vector store by ID."""
        query = """
            SELECT * FROM vector_stores
            WHERE id = $1 AND api_key = $2
        """
        
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, vector_store_id, api_key)

        # Raise exception
        if not row: raise VectorStoreNotFoundException(vector_store_id)

        record = dict(row)
        # Normalize JSONB fields to Python types
        for key in ("metadata", "expires_after", "chunking_strategy", "file_ids"):
            value = record.get(key)
            if isinstance(value, str):
                try:
                    record[key] = json.loads(value)
                except json.JSONDecodeError:
                    record[key] = None
        return record

    @staticmethod
    async def update(pool: asyncpg.Pool,
                     vector_store_id: str,
                     api_key: str,
                     *,
                     name: Optional[str] = None,
                     status: Optional[str] = None,
                     last_active_at: Optional[Any] = None,
                     usage_bytes: Optional[int] = None,
                     metadata: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Update a vector store with the specified fields."""
        updates = []
        params = []

        # Add fields to update if they are not None
        if name is not None:
            updates.append("name = ${}".format(len(params) + 1))
            params.append(name)

        if status is not None:
            updates.append("status = ${}".format(len(params) + 1))
            params.append(status)

        if last_active_at is not None:
            updates.append("last_active_at = ${}::timestamptz".format(len(params) + 1))
            params.append(last_active_at)

        if usage_bytes is not None:
            updates.append("usage_bytes = ${}".format(len(params) + 1))
            params.append(usage_bytes)

        if metadata is not None:
            updates.append("metadata = ${}::jsonb".format(len(params) + 1))
            params.append(json.dumps(metadata))

        if not updates:
            raise ValueError("No fields to update")

        # Add the WHERE clause parameters
        updates_str = ", ".join(updates)
        query = f"""
            UPDATE vector_stores
            SET {updates_str}
            WHERE id = ${len(params) + 1} AND api_key = ${len(params) + 2}
            RETURNING *
        """
        params.extend([vector_store_id, api_key])

        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)

        # Raise exception if not found
        if not row:
            raise VectorStoreNotFoundException(vector_store_id)

        record = dict(row)
        # Normalize JSONB fields to Python types
        for key in ("metadata", "expires_after", "chunking_strategy", "file_ids"):
            value = record.get(key)
            if isinstance(value, str):
                try:
                    record[key] = json.loads(value)
                except json.JSONDecodeError:
                    record[key] = None
        return record

    @staticmethod
    async def delete(pool: asyncpg.Pool,
                     vector_store_id: str,
                     api_key: str) -> None:
        """Delete a vector store."""
        query = """
            DELETE FROM vector_stores
            WHERE id = $1 AND api_key = $2
        """
        # Handle the deletion
        async with pool.acquire() as conn:
            result = await conn.execute(query, vector_store_id, api_key)

    @staticmethod
    async def list(pool: asyncpg.Pool,
                   api_key: str,
                   limit: int = 20,
                   order: str = "desc",
                   after: Optional[str] = None,
                   before: Optional[str] = None) -> Dict[str, Any]:
        """List vector stores with pagination."""
        if order not in ("asc", "desc"):
            raise ValueError("order must be 'asc' or 'desc'")

        query = """
            SELECT * FROM vector_stores
            WHERE api_key = $1
        """
        params = [api_key]
        
        if after:
            query += f" AND id > ${len(params) + 1}"
            params.append(after)
        if before:
            query += f" AND id < ${len(params) + 1}"
            params.append(before)
        
        query += f" ORDER BY created_at {order.upper()}"
        query += f" LIMIT ${len(params) + 1}"
        params.append(limit)

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        vector_stores = []
        for row in rows:
            record = dict(row)
            # Normalize JSONB fields to Python types
            for key in ("metadata", "chunking_strategy", "file_ids"):
                value = record.get(key)
                if isinstance(value, str):
                    try:
                        record[key] = json.loads(value)
                    except json.JSONDecodeError:
                        record[key] = None
            vector_stores.append(record)

        return {
            "object": "list",
            "data": vector_stores,
            "first_id": vector_stores[0]["id"] if vector_stores else None,
            "last_id": vector_stores[-1]["id"] if vector_stores else None,
            "has_more": len(vector_stores) == limit
        }
