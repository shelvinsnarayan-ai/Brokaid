"""Phase R5c-3/R5c-4a — Mongo-only advisory locks.

Uses `safe.system("_locks")` (allow-listed) with idempotent upsert +
TTL reclaim. Same semantics as the R5c-3 Lock class; the PG branch
was deleted in R5c-4a.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap as _wrap_safe


logger = logging.getLogger("brokaid.locks")


def _mongo_key(key: int) -> str:
    return f"lock:{key & 0xFFFFFFFFFFFFFFFF:016x}"


@dataclass
class Lock:
    store: Any
    key: int
    holder_id: str
    ttl_sec: float
    acquired: bool = field(default=False, init=False)

    async def try_acquire(self) -> bool:
        safe = _wrap_safe(self.store)
        mkey = _mongo_key(self.key)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.ttl_sec)
        doc = {
            "_id": mkey, "holder_id": self.holder_id,
            "acquired_at": now, "expires_at": expires,
        }
        try:
            await safe.system("_locks").insert_one(doc)
            self.acquired = True
            return True
        except DuplicateKeyError:
            pass
        result = await safe.system("_locks").update_one(
            {"_id": mkey, "expires_at": {"$lt": now}},
            {"$set": {
                "holder_id": self.holder_id,
                "acquired_at": now, "expires_at": expires,
            }},
        )
        self.acquired = result.modified_count == 1
        return self.acquired

    async def release(self) -> None:
        if not self.acquired:
            return
        safe = _wrap_safe(self.store)
        mkey = _mongo_key(self.key)
        try:
            await safe.system("_locks").delete_one(
                {"_id": mkey, "holder_id": self.holder_id},
            )
        finally:
            self.acquired = False


__all__ = ["Lock"]
