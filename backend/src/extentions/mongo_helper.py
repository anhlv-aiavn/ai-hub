# src/db/mongo_common.py
import asyncio
import logging
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import BulkWriteError
from pymongo import UpdateOne, ReturnDocument
import urllib.parse
import os
from dotenv import load_dotenv
# Load variables from .env into the environment
load_dotenv()


class AsyncMongo:
    def __init__(self, connection_string: str = None, database_name: str = None):
        self.connection_string = connection_string or os.getenv("MONGO_URI")
        self.database_name = database_name or os.getenv("MONGO_DB")

        self.client: AsyncIOMotorClient = AsyncIOMotorClient(self.connection_string)
        self.db: AsyncIOMotorDatabase = self.client[self.database_name]
        self.logger = logging.getLogger(__name__)

        self._index_ensured = False

    # ==============================
    # Async CRUD Methods
    # ==============================

    async def insert_one(self, collection_name: str, document: Dict[str, Any]) -> Optional[str]:
        try:
            result = await self.db[collection_name].insert_one(document)
            return str(result.inserted_id)
        except Exception as e:
            self.logger.error(f"Error inserting document: {e}")
            return None

    async def insert_many(
        self, collection_name: str, documents: List[Dict[str, Any]]
    ) -> Optional[List[str]]:
        try:
            result = await self.db[collection_name].insert_many(documents, ordered=False)
            return [str(_id) for _id in result.inserted_ids]
        except Exception as e:
            self.logger.error(f"Error inserting many documents: {e}")
            return None

    async def find_one(
        self, collection_name: str, query: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.db[collection_name].find_one(query)
        except Exception as e:
            self.logger.error(f"Error finding document: {e}")
            return None

    async def find_many(
        self,
        collection_name: str,
        query: Dict[str, Any],
        limit: int = 0,
        skip: int = 0,
        sort: Optional[List[tuple]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            cursor = self.db[collection_name].find(query)
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit > 0:
                cursor = cursor.limit(limit)
            if sort:
                cursor = cursor.sort(sort)
            return await cursor.to_list(length=limit or 1000)
        except Exception as e:
            self.logger.error(f"Error finding documents: {e}")
            return []

    async def find_many_iter(
        self,
        collection_name: str,
        query: dict,
        batch_size: int = 1000,
        sort: list | None = None,
    ):
        try:
            cursor = self.db[collection_name].find(query, batch_size=batch_size)
            if sort:
                cursor = cursor.sort(sort)
            async for doc in cursor:
                yield doc
        except Exception as e:
            self.logger.error(f"Error iterating documents: {e}")
            return

    async def update_one(
        self, collection_name: str, query: Dict[str, Any], update: Dict[str, Any]
    ) -> bool:
        """
        Update one document.
        - Nếu update đã chứa MongoDB operators ($set, $inc, ...) → dùng trực tiếp.
        - Nếu không → tự wrap vào $set (backward-compatible với code cũ).
        """
        try:
            has_operator = any(k.startswith("$") for k in update)
            payload = update if has_operator else {"$set": update}
            result = await self.db[collection_name].update_one(query, payload)
            return result.modified_count > 0
        except Exception as e:
            self.logger.error(f"Error updating document: {e}")
            return False

    async def raw_update_one(
        self, collection_name: str, query: Dict[str, Any], update: Dict[str, Any]
    ) -> bool:
        """
        Raw update: caller passes the full update document (e.g. {"$set": ..., "$inc": ...}).
        Use this when you need operators other than $set.
        """
        try:
            result = await self.db[collection_name].update_one(query, update)
            return result.modified_count > 0
        except Exception as e:
            self.logger.error(f"Error raw-updating document: {e}")
            return False

    async def find_one_and_update(
        self,
        collection_name: str,
        query: Dict[str, Any],
        update: Dict[str, Any],
        return_document: bool = True,
        upsert: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Atomic find-and-update.
        - return_document=True  → returns the document AFTER the update (default)
        - return_document=False → returns the document BEFORE the update
        - update must be a full MongoDB update document (e.g. {"$set": ..., "$inc": ...})
        """
        try:
            which = ReturnDocument.AFTER if return_document else ReturnDocument.BEFORE
            return await self.db[collection_name].find_one_and_update(
                query,
                update,
                return_document=which,
                upsert=upsert,
            )
        except Exception as e:
            self.logger.error(f"Error in find_one_and_update: {e}")
            return None

    async def update_many(
        self, collection_name: str, query: Dict[str, Any], update: Dict[str, Any]
    ) -> int:
        try:
            has_operator = any(k.startswith("$") for k in update)
            payload = update if has_operator else {"$set": update}
            result = await self.db[collection_name].update_many(query, payload)
            return result.modified_count
        except Exception as e:
            self.logger.error(f"Error updating many documents: {e}")
            return 0

    async def delete_one(self, collection_name: str, query: Dict[str, Any]) -> bool:
        try:
            result = await self.db[collection_name].delete_one(query)
            return result.deleted_count > 0
        except Exception as e:
            self.logger.error(f"Error deleting document: {e}")
            return False

    async def delete_many(self, collection_name: str, query: Dict[str, Any]) -> int:
        try:
            result = await self.db[collection_name].delete_many(query)
            return result.deleted_count
        except Exception as e:
            self.logger.error(f"Error deleting many documents: {e}")
            return 0

    # ==============================
    # Async Bulk Operations
    # ==============================

    async def bulk_upsert(
        self,
        collection_name: str,
        documents: List[Dict[str, Any]],
    ) -> int:
        if not documents:
            return 0
        try:
            result = await self.db[collection_name].insert_many(documents, ordered=False)
            return len(result.inserted_ids)
        except BulkWriteError as bwe:
            write_errors = bwe.details.get("writeErrors", [])
            duplicate_count = sum(1 for err in write_errors if err.get("code") == 11000)
            inserted_count = bwe.details.get("nInserted", 0)
            self.logger.info(
                f"Skipped {duplicate_count} duplicates, inserted {inserted_count} new docs."
            )
            return inserted_count
        except Exception as e:
            self.logger.error(f"Bulk upsert failed: {e}")
            return 0

    async def bulk_update(
        self,
        collection_name: str,
        updates: List[Dict[str, Any]],
    ) -> int:
        if not updates:
            return 0
        try:
            operations = [
                UpdateOne(u["filter"], {"$set": u["update"]}, upsert=u.get("upsert", False))
                for u in updates
            ]
            result = await self.db[collection_name].bulk_write(operations, ordered=False)
            return result.modified_count + len(result.upserted_ids or {})
        except Exception as e:
            self.logger.error(f"Bulk update failed: {e}")
            return 0

    async def count_documents(self, collection_name: str, query: Dict[str, Any] = None) -> int:
        try:
            query = query or {}
            output = await self.db[collection_name].count_documents(query)
        except Exception as e:
            self.logger.error(f"Error counting documents: {e}")
            return 0

    # ==============================
    # Connection Management
    # ==============================

    async def close_connection(self):
        self.client.close()
        self.logger.info("MongoDB connection closed.")

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except Exception:
            return False

    async def bulk_write_ops(
        self,
        collection_name: str,
        operations: List[UpdateOne],  # pymongo.UpdateOne / DeleteOne / InsertOne ...
    ) -> int:
        """
        Thực thi danh sách raw pymongo write operations (UpdateOne, InsertOne, ...).
        Khác bulk_update: caller tự build op, không bị wrap $set cứng.
        Dùng cho upsert phức tạp như $addToSet + $setOnInsert.
 
        Trả về: số documents bị affected (upserted + modified).
        """
        if not operations:
            return 0
        try:
            result = await self.db[collection_name].bulk_write(operations, ordered=False)
            return result.upserted_count + result.modified_count
        except BulkWriteError as e:
            write_errors = e.details.get("writeErrors", [])
            real_errors = [err for err in write_errors if err.get("code") != 11000]
            n = e.details.get("nModified", 0) + e.details.get("nUpserted", 0)
            if real_errors:
                self.logger.warning(
                    f"bulk_write_ops partial error on '{collection_name}': "
                    f"affected={n}, non-duplicate errors={len(real_errors)}"
                )
            return n
        except Exception as e:
            self.logger.exception(f"bulk_write_ops failed on '{collection_name}': {e}")
            return 0
        
    async def aggregate(
        self,
        collection_name: str,
        pipeline: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Chạy aggregation pipeline và trả về list kết quả."""
        try:
            cursor = self.db[collection_name].aggregate(pipeline)
            return await cursor.to_list(length=None)
        except Exception as e:
            self.logger.error(f"Error running aggregation: {e}")
            return []

    async def group_by_gcn_id(
        self,
        collection_name: str,
        min_count: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        Group documents theo gcn_id, chỉ trả về các nhóm có số lượng >= min_count.

        Kết quả mỗi phần tử:
        {
            "gcn_id": "0101040009",
            "count": 3,
            "docs": [ { ...doc1... }, { ...doc2... }, ... ]
        }
        """
        pipeline = [
            {"$match": {"gcn_id": {"$exists": True, "$ne": None}}},
            {
                "$group": {
                    "_id": "$gcn_id",
                    "count": {"$sum": 1},
                    "docs": {"$push": "$$ROOT"},
                }
            },
            {"$match": {"count": {"$gte": min_count}}},
            {
                "$project": {
                    "_id": 0,
                    "gcn_id": "$_id",
                    "count": 1,
                    "docs": 1,
                }
            },
            {"$sort": {"count": -1}},
        ]
        return await self.aggregate(collection_name, pipeline)
        

async def main():
    mongo = AsyncMongo()
    duplicates = await mongo.group_by_gcn_id("hsq-hni", min_count=2)

    for group in duplicates:
        print(f"gcn_id={group['gcn_id']}  count={group['count']}")
        for doc in group["docs"]:
            print("  ", doc["_id"], doc.get("file_hsq_key"))

            break

if __name__ == "__main__":
    asyncio.run(main())