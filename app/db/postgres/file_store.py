# Typing
from typing import List, Dict, Sequence
from datetime import datetime
# Table
from .tables import *
# Exceptions
from app.exceptions.file import FileNotFoundException
# Schema
import asyncpg, json

class PostgresFileStore:
    @staticmethod
    async def _create_table(pool: asyncpg.Pool):
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Create assistant table
                await conn.execute(CREATE_FILE_TABLE)
                # Create run indexes
                await conn.execute(CREATE_FILE_INDEXES)

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
        """Insert a new file record."""

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
        - after: object ID (cursor)
        - limit: max rows (1–10,000)
        - order: 'asc' or 'desc' by created_at
        - purpose: filter by file purpose
        """

        order = order.lower()
        if order not in ("asc", "desc"):
            order = "desc"

        # Build base query dynamically
        conditions = ["api_key = $1"]
        params = [api_key]
        param_idx = 2

        # Optional purpose filter
        if purpose:
            conditions.append(f"purpose = ${param_idx}")
            params.append(purpose)
            param_idx += 1

        # Handle pagination cursor
        if after:
            # Fetch created_at of cursor file first
            async with pool.acquire() as conn:
                after_row = await conn.fetchrow(
                    "SELECT created_at, id FROM files WHERE id = $1", after
                )
                if not after_row:
                    raise ValueError(f"Cursor file id '{after}' not found")

                after_created_at = after_row["created_at"]
                after_id = after_row["id"]

            # Pagination logic depends on sort order
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
            # Safely ensure metadata is a dict
            if isinstance(record.get("metadata"), str):
                try:
                    record["metadata"] = json.loads(record["metadata"])
                except json.JSONDecodeError:
                    record["metadata"] = None
            result.append(record)

        return result

    @staticmethod
    async def get_file_by_id(pool: asyncpg.Pool,
                             file_id: str) -> dict:
        """
        Retrieve a single file by its ID.
        Raises 404 if the file does not exist.
        """
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM files WHERE id = $1;", file_id)

            # When file id not found
            if not row: raise FileNotFoundException(file_id = file_id)
            # Return
            file_data = dict(row)

            # Ensure metadata is a dict (convert from string if needed)
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
        Delete a file by its ID.
        Returns True if deleted, raises 404 if not found.
        """
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Fetch metadata before deletion
                row = await conn.fetchrow("SELECT metadata FROM files WHERE id = $1 AND api_key = $2;",
                                          file_id,
                                          api_key)

                # When not found
                if not row: raise FileNotFoundException(file_id = file_id)
                # Metadata
                metadata = row["metadata"]

                # Convert metadata to dict if stored as JSON string
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = None

                # Delete the row
                await conn.execute("DELETE FROM files WHERE id = $1;", file_id)

                return {"metadata": metadata, "deleted": True}

    @staticmethod
    async def check_existing_files(pool: asyncpg.Pool, file_ids: list[str]) -> list[str]:
        """
        Check which file IDs from the input list exist in the database.
        
        Args:
            pool: Database connection pool
            file_ids: List of file IDs to check
            
        Returns:
            List of file IDs that exist in the database
        """
        if not file_ids:
            return []
            
        # Create a parameterized query with placeholders ($1, $2, etc.)
        placeholders = ','.join(f'${i+1}' for i in range(len(file_ids)))
        query = f"SELECT id FROM files WHERE id IN ({placeholders})"
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *file_ids)
        
        # Return list of existing file IDs
        return [row['id'] for row in rows]

    @staticmethod
    async def _get_metadata_for_files(pool: asyncpg.Pool,
                                      file_ids: Sequence[str]) -> List[Dict]:
        """
        Return list of metadata objects for given file_ids.
        Order is not guaranteed unless ORDER BY is added.
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
    async def _get_total_bytes_for_file_ids(pool: asyncpg.Pool, file_ids: [str]) -> int:
        """
        Return the total bytes for the given list of file IDs.

        Args:
            pool: asyncpg.Pool
            file_ids: sequence of file id strings

        Returns:
            int: sum of bytes for matching files (0 if none)
        """
        ids = list(file_ids)
        if not ids:
            return 0

        query = """
                SELECT COALESCE(SUM(bytes), 0)::bigint
                FROM files
                WHERE id = ANY($1::text[]);
            """

        async with pool.acquire() as conn:
            # transaction optional here, but kept for parity with other methods
            async with conn.transaction():
                total = await conn.fetchval(query, ids)
                return int(total or 0)
