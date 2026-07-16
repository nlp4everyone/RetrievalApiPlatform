# Typing
from typing import List, Dict, Sequence, Any
from datetime import datetime
# Exceptions
from app.exceptions.file import FileNotFoundException
# Schema
import asyncpg, json

class PostgresFileStore:
    @staticmethod
    def _parse_json_field(value: Any) -> Any:
        """Parse JSON string to Python object if needed.
        
        Args:
            value: Value that might be a JSON string
            
        Returns:
            Parsed Python object or None if parsing fails
        """
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return value

    @staticmethod
    async def insert_file(pool: asyncpg.Pool,
                          id: str,
                          api_key: str,
                          bytes: int,
                          purpose: str,
                          created_at: datetime,
                          expires_at: datetime = None,
                          content_type: str = None,
                          metadata: dict = None):
        """
        Insert a new file record into the database.
        
        Args:
            pool: Database connection pool
            id: Unique file identifier
            api_key: API key that owns this file
            bytes: File size in bytes
            purpose: File purpose (e.g., 'assistants', 'fine-tune')
            created_at: File creation timestamp
            expires_at: Optional expiration timestamp
            content_type: Optional MIME content type
            metadata: Optional metadata dictionary (stored as JSON)
        """

        query = """
            INSERT INTO files (
                id,
                api_key,
                bytes,
                purpose,
                created_at,
                expires_at,
                content_type,
                metadata
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8
            )
        """

        async with pool.acquire() as conn:
            await conn.execute(
                query,
                id,
                api_key,
                bytes,
                purpose,
                created_at,
                expires_at,
                content_type,
                json.dumps(metadata) if metadata is not None else None
            )

    @staticmethod
    async def list_files(pool: asyncpg.Pool,
                         api_key: str,
                         after: str = None,
                         limit: int = 10000,
                         order: str = "desc",
                         purpose: str = None):
        """
        List files for an API key with optional pagination and filtering.
        
        Args:
            pool: Database connection pool
            api_key: API key to filter files by
            after: Optional cursor (file ID) for pagination
            limit: Maximum number of rows to return (1-10,000)
            order: Sort order ('asc' or 'desc') by created_at
            purpose: Optional filter by file purpose
            
        Returns:
            List of file dictionaries with metadata parsed from JSON
            
        Raises:
            ValueError: If pagination cursor file is not found
        """

        # Validate and normalize sort order
        order = order.lower()
        if order not in ("asc", "desc"):
            order = "desc"

        # Build WHERE clause dynamically based on filters
        conditions = ["api_key = $1"]
        params = [api_key]
        param_idx = 2

        # Add purpose filter if specified
        if purpose:
            conditions.append(f"purpose = ${param_idx}")
            params.append(purpose)
            param_idx += 1

        # Handle cursor-based pagination
        if after:
            # Fetch cursor file's timestamp for pagination
            async with pool.acquire() as conn:
                after_row = await conn.fetchrow(
                    "SELECT created_at, id FROM files WHERE id = $1", after
                )
                if not after_row:
                    raise ValueError(f"Cursor file id '{after}' not found")

                after_created_at = after_row["created_at"]
                after_id = after_row["id"]

            # Apply pagination conditions based on sort order
            if order == "desc":
                conditions.append(
                    f"(created_at < ${param_idx} OR (created_at = ${param_idx} AND id < ${param_idx + 1}))"
                )
            else:
                conditions.append(
                    f"(created_at > ${param_idx} OR (created_at = ${param_idx} AND id > ${param_idx + 1}))"
                )

            params.extend([after_created_at, after_id])
            param_idx += 2

        where_clause = " AND ".join(conditions)

        query = f"""
                SELECT id, api_key, bytes, purpose, created_at, expires_at, content_type, metadata
                FROM files
                WHERE {where_clause}
                ORDER BY created_at {order.upper()}, id {order.upper()}
                LIMIT ${param_idx};
            """

        params.append(limit)

        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        result = []
        for r in rows:
            record = dict(r)
            # Convert metadata from JSON string to dict for each record
            record["metadata"] = PostgresFileStore._parse_json_field(record.get("metadata"))
            result.append(record)

        return result

    @staticmethod
    async def get_file_by_id(pool: asyncpg.Pool,
                             file_id: str) -> dict:
        """
        Retrieve a single file by its ID.
        
        Args:
            pool: Database connection pool
            file_id: Unique file identifier to retrieve
            
        Returns:
            Dictionary containing all file fields with metadata parsed from JSON
            
        Raises:
            FileNotFoundException: If the file does not exist
        """
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM files WHERE id = $1;", file_id)

        # Check if file exists and raise appropriate error if not
        if not row: 
            raise FileNotFoundException(file_id = file_id)
        
        # Convert row to dictionary
        file_data = dict(row)

        # Parse metadata from JSON string to dictionary if needed
        metadata = file_data.get("metadata")
        if isinstance(metadata, str):
            try:
                file_data["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                file_data["metadata"] = None  # fallback if invalid JSON

        return file_data

    @staticmethod
    async def delete_file_by_id(pool: asyncpg.Pool,
                                file_id: str,
                                api_key: str) -> dict:
        """
        Delete a file by its ID and API key.
        
        Args:
            pool: Database connection pool
            file_id: Unique file identifier to delete
            api_key: API key that owns the file (for security)
            
        Returns:
            Dictionary containing the deleted file's metadata and deletion status
            
        Raises:
            FileNotFoundException: If the file does not exist or doesn't belong to the API key
        """
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Retrieve metadata before deleting the file
                row = await conn.fetchrow("SELECT metadata FROM files WHERE id = $1 AND api_key = $2;",
                                          file_id,
                                          api_key)

                # Verify file exists and belongs to the specified API key
                if not row: raise FileNotFoundException(file_id = file_id)
                # Metadata
                metadata = row["metadata"]

                # Parse metadata from JSON string if needed
                metadata = PostgresFileStore._parse_json_field(metadata)

                # Execute the deletion
                await conn.execute("DELETE FROM files WHERE id = $1;", file_id)

                return {"metadata": metadata, "deleted": True}

    @staticmethod
    async def check_existing_files(pool: asyncpg.Pool, file_ids: list[str]) -> list[str]:
        """
        Check which file IDs from the input list exist in the database.
        
        Args:
            pool: Database connection pool
            file_ids: List of file IDs to check for existence
            
        Returns:
            List of file IDs that exist in the database
            
        Note:
            Returns empty list if input list is empty
        """
        if not file_ids:
            return []
            
        # Build parameterized query to prevent SQL injection
        placeholders = ','.join(f'${i+1}' for i in range(len(file_ids)))
        query = f"SELECT id FROM files WHERE id IN ({placeholders})"
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *file_ids)
        
        # Extract existing file IDs from query results
        return [row['id'] for row in rows]

    @staticmethod
    async def get_metadata_for_files(pool: asyncpg.Pool,
                                     file_ids: Sequence[str]) -> List[Dict]:
        """
        Retrieve metadata for multiple files by their IDs.
        
        Args:
            pool: Database connection pool
            file_ids: Sequence of file IDs to retrieve metadata for
            
        Returns:
            List of metadata dictionaries for files that exist and have metadata
            
        Note:
            - Returns empty list if input is empty
            - Order of returned metadata is not guaranteed
            - Files without metadata are excluded from results
        """
        if not file_ids:
            return []

        query = """
            SELECT metadata
            FROM files
            WHERE id = ANY($1::text[]);
        """

        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(query, list(file_ids))
                return [json.loads(row["metadata"]) for row in rows if row["metadata"] is not None]

    @staticmethod
    async def get_total_bytes(pool: asyncpg.Pool, file_ids: [str]) -> int:
        """
        Calculate the total storage size for the given list of file IDs.
        
        Args:
            pool: Database connection pool
            file_ids: Sequence of file ID strings to calculate size for
            
        Returns:
            Total bytes used by the specified files (0 if none found)
        """
        # Convert to list for database query and handle empty case
        ids = list(file_ids)
        if not ids:
            return 0

        query = """
                SELECT COALESCE(SUM(bytes), 0)::bigint
                FROM files
                WHERE id = ANY($1::text[]);
            """

        async with pool.acquire() as conn:
            # Transaction used for consistency with other methods
            async with conn.transaction():
                total = await conn.fetchval(query, ids)
                return int(total or 0)
