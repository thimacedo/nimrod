"""
Dead Letter Queue module for tracking failed scraping targets.
Stores targets that couldn't be processed for later retry.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dead_letter_queue")

# Default storage path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DLQ_FILE = os.path.join(BASE_DIR, "data", "dead_letter_queue.json")


class DeadLetterQueue:
    """
    Persistent dead letter queue for failed scraping targets.
    Stores information about targets that couldn't be processed
    so they can be retried later.
    """

    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or DLQ_FILE
        self._queue: List[Dict[str, Any]] = []
        self._loaded = False

    async def _ensure_loaded(self):
        """Load the queue from disk if not already loaded."""
        if self._loaded:
            return
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self._queue = json.load(f)
            self._loaded = True
        except Exception as e:
            logger.error(f"Failed to load DLQ from {self.storage_path}: {e}")
            self._queue = []
            self._loaded = True

    async def _save(self):
        """Save the queue to disk."""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._queue, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save DLQ to {self.storage_path}: {e}")

    async def add_failed_target(
        self,
        target_username: str,
        error_type: str,
        error_message: str,
        original_target_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Add a failed target to the dead letter queue.
        
        Args:
            target_username: The Instagram username that failed
            error_type: Type of error (e.g., "DOM_HEALING_FAILED")
            error_message: Description of the error
            original_target_id: Original target ID if available
            metadata: Additional metadata to store
            
        Returns:
            True if successfully added, False otherwise
        """
        await self._ensure_loaded()
        
        entry = {
            "target_username": target_username,
            "error_type": error_type,
            "error_message": error_message,
            "original_target_id": original_target_id,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "retry_count": 0,
        }
        
        self._queue.append(entry)
        
        try:
            await self._save()
            logger.info(f"Added {target_username} to DLQ with error: {error_type}")
            return True
        except Exception as e:
            logger.error(f"Failed to save entry to DLQ: {e}")
            return False

    async def get_all(self) -> List[Dict[str, Any]]:
        """Get all entries from the dead letter queue."""
        await self._ensure_loaded()
        return self._queue.copy()

    async def get_by_type(self, error_type: str) -> List[Dict[str, Any]]:
        """Get all entries of a specific error type."""
        await self._ensure_loaded()
        return [e for e in self._queue if e.get("error_type") == error_type]

    async def remove(self, index: int) -> bool:
        """Remove an entry by index."""
        await self._ensure_loaded()
        if 0 <= index < len(self._queue):
            self._queue.pop(index)
            await self._save()
            return True
        return False

    async def clear(self) -> bool:
        """Clear all entries from the queue."""
        self._queue = []
        try:
            await self._save()
            return True
        except Exception as e:
            logger.error(f"Failed to clear DLQ: {e}")
            return False

    async def increment_retry(self, index: int) -> bool:
        """Increment the retry count for an entry."""
        await self._ensure_loaded()
        if 0 <= index < len(self._queue):
            self._queue[index]["retry_count"] += 1
            await self._save()
            return True
        return False


# Global instance
dead_letter_queue = DeadLetterQueue()
