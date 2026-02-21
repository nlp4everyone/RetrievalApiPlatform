# Typing
from typing import Any, Dict, Optional
# Exception
from app.exceptions.vector_store import VectorStoreNotFoundException
# Other component
import asyncpg, json

class PostgresVectorStore:
    @staticmethod
    async def _check_vector_store_existence(pool: asyncpg.Pool,
                                            vector_store_id: str,
                                            api_key: str):
        """
        Private helper method to verify vector store exists.
        
        Args:
            pool: PostgreSQL connection pool
            vector_store_id: Unique identifier of the vector store
            api_key: API key for authentication
            
        Raises:
            VectorStoreNotFoundException: If vector store doesn't exist
        """
        # Define SQL query to check existence
        query = """
        SELECT * FROM vector_stores
        WHERE id = $1 AND api_key = $2
        """

        # Execute query with connection from pool
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, vector_store_id, api_key)

        # Raise exception if vector store not found
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
        """
        Create a new vector store record in the database.
        
        Args:
            pool: PostgreSQL connection pool
            id: Unique identifier for the vector store
            api_key: API key for authentication and ownership
            name: Human-readable name for the vector store
            description: Optional description of the vector store purpose
            created_at: Timestamp when the vector store was created
            last_active_at: Timestamp of last activity
            status: Current status (e.g., 'active', 'inactive', 'processing')
            usage_bytes: Current storage usage in bytes
            metadata: Optional key-value metadata dictionary
            expires_at: Optional expiration timestamp
            expires_after: Optional expiration period in seconds
            chunking_strategy: Optional chunking configuration
            vector_store_type: Optional type classification
            
        Returns:
            Dict containing the complete created vector store record
            
        Raises:
            asyncpg.PostgresError: If database operation fails
        """

        # Build INSERT query with all vector store fields
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

        # Execute insert with connection from pool
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

        # Convert result to dictionary and normalize JSONB fields
        record = dict(row)
        # Convert JSONB strings back to Python objects for consistency
        for key in ("metadata", "chunking_strategy"):
            value = record.get(key)
            if isinstance(value, str):
                try:
                    record[key] = json.loads(value)
                except json.JSONDecodeError:
                    record[key] = None
        return record

    @staticmethod
    async def get_by_id(pool: asyncpg.Pool,
                       vector_store_id: str,
                       api_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a vector store by its unique identifier.
        
        Args:
            pool: PostgreSQL connection pool
            vector_store_id: Unique identifier of the vector store to retrieve
            api_key: API key for authentication
            
        Returns:
            Dict containing the vector store record with JSONB fields normalized to Python types
            
        Raises:
            VectorStoreNotFoundException: If vector store doesn't exist
            asyncpg.PostgresError: If database operation fails
        """
        # Build SELECT query for specific vector store
        query = """
            SELECT * FROM vector_stores
            WHERE id = $1 AND api_key = $2
        """
        
        # Execute query with connection from pool
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, vector_store_id, api_key)

        # Raise exception if vector store not found
        if not row: raise VectorStoreNotFoundException(vector_store_id)

        # Convert result to dictionary and normalize JSONB fields
        record = dict(row)
        # Convert JSONB strings back to Python objects for consistency
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
        """
        Update specific fields of an existing vector store.
        
        Only non-None fields will be updated. At least one field must be provided.
        
        Args:
            pool: PostgreSQL connection pool
            vector_store_id: Unique identifier of the vector store to update
            api_key: API key for authentication
            name: New name for the vector store (optional)
            status: New status for the vector store (optional)
            last_active_at: New last activity timestamp (optional)
            usage_bytes: New usage in bytes (optional)
            metadata: New metadata dictionary (optional)
            
        Returns:
            Dict containing the updated vector store record
            
        Raises:
            VectorStoreNotFoundException: If vector store doesn't exist
            ValueError: If no fields are provided for update
            asyncpg.PostgresError: If database operation fails
        """
        # Initialize lists for dynamic query building
        updates = []
        params = []

        # Build UPDATE clauses dynamically based on provided fields
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

        # Validate that at least one field is being updated
        if not updates:
            raise ValueError("No fields to update")

        # Construct the complete UPDATE query with dynamic clauses
        updates_str = ", ".join(updates)
        query = f"""
            UPDATE vector_stores
            SET {updates_str}
            WHERE id = ${len(params) + 1} AND api_key = ${len(params) + 2}
            RETURNING *
        """
        # Add WHERE clause parameters
        params.extend([vector_store_id, api_key])

        # Execute update with connection from pool
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)

        # Raise exception if vector store not found
        if not row:
            raise VectorStoreNotFoundException(vector_store_id)

        # Convert result to dictionary and normalize JSONB fields
        record = dict(row)
        # Convert JSONB strings back to Python objects for consistency
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
        """
        Delete a vector store from the database.
        
        This method first checks if the vector store exists before attempting deletion
        to provide consistent error handling with other methods.
        
        Args:
            pool: PostgreSQL connection pool
            vector_store_id: Unique identifier of the vector store to delete
            api_key: API key for authentication
            
        Raises:
            VectorStoreNotFoundException: If vector store doesn't exist
            asyncpg.PostgresError: If database operation fails
        """
        # Check if vector store exists first for consistent error handling
        await PostgresVectorStore._check_vector_store_existence(pool=pool,
                                                                vector_store_id=vector_store_id,
                                                                api_key=api_key)
        
        # Build DELETE query for specific vector store
        query = """
            DELETE FROM vector_stores
            WHERE id = $1 AND api_key = $2
        """
        # Execute deletion with connection from pool
        async with pool.acquire() as conn:
            result = await conn.execute(query, vector_store_id, api_key)

    @staticmethod
    async def list_vector_stores(pool: asyncpg.Pool,
                                 api_key: str,
                                 limit: int = 20,
                                 order: str = "desc",
                                 after: Optional[str] = None,
                                 before: Optional[str] = None) -> Dict[str, Any]:
        """
        List vector stores with cursor-based pagination.
        
        Supports pagination using cursor-based navigation with 'after' and 'before'
        parameters for efficient traversal of large datasets.
        
        Args:
            pool: PostgreSQL connection pool
            api_key: API key for authentication (filters by owner)
            limit: Maximum number of results to return (default: 20)
            order: Sort order - 'asc' for ascending, 'desc' for descending (default: "desc")
            after: Cursor ID for pagination - get results after this ID
            before: Cursor ID for pagination - get results before this ID
            
        Returns:
            Dict containing:
            - 'object': Always 'list'
            - 'data': List of vector store records
            - 'first_id': ID of first record in results
            - 'last_id': ID of last record in results  
            - 'has_more': Boolean indicating if more results available
            
        Raises:
            ValueError: If order parameter is not 'asc' or 'desc'
            asyncpg.PostgresError: If database operation fails
        """
        # Validate sort order parameter
        if order not in ("asc", "desc"):
            raise ValueError("order must be 'asc' or 'desc'")

        # Build base query with API key filter
        query = """
            SELECT * FROM vector_stores
            WHERE api_key = $1
        """
        params = [api_key]
        
        # Add cursor-based pagination filters
        if after:
            query += f" AND id > ${len(params) + 1}"
            params.append(after)
        if before:
            query += f" AND id < ${len(params) + 1}"
            params.append(before)
        
        # Add sorting and limit
        query += f" ORDER BY created_at {order.upper()}"
        query += f" LIMIT ${len(params) + 1}"
        params.append(limit)

        # Execute query with connection from pool
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        # Process results and normalize JSONB fields
        vector_stores = []
        for row in rows:
            record = dict(row)
            # Convert JSONB strings back to Python objects for consistency
            for key in ("metadata", "chunking_strategy", "file_ids"):
                value = record.get(key)
                if isinstance(value, str):
                    try:
                        record[key] = json.loads(value)
                    except json.JSONDecodeError:
                        record[key] = None
            vector_stores.append(record)

        # Build pagination response
        return {
            "object": "list",
            "data": vector_stores,
            "first_id": vector_stores[0]["id"] if vector_stores else None,
            "last_id": vector_stores[-1]["id"] if vector_stores else None,
            "has_more": len(vector_stores) == limit
        }
