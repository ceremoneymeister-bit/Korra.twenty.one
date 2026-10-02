"""Receipts of Telegram updates already taken in, kept on disk per bot.

Telegram redelivers an update until the bot confirms a later offset. After a
crash or a reconnect that confirmation can be lost, and the same message would
get a second answer. A receipt is one line, ``<update_id> <unix_ts>``, appended
the moment an update is admitted; lines older than the TTL are dropped on load.
The file is plain text next to the profile data, so older code simply ignores it.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

RECEIPT_TTL_SECONDS = 24 * 3600


class UpdateReceipts:
    def __init__(self, path: Path, ttl: float = RECEIPT_TTL_SECONDS) -> None:
        self._path = path
        self._ttl = ttl
        self._seen: Dict[int, float] = {}
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        now = time.time()
        expired = False
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        except OSError:
            logger.warning("Telegram update receipts unreadable: %s", self._path, exc_info=True)
            return
        for line in lines:
            parts = line.split()
            try:
                update_id, ts = int(parts[0]), float(parts[1])
            except (IndexError, ValueError):
                expired = True
                continue
            if now - ts > self._ttl:
                expired = True
                continue
            self._seen[update_id] = ts
        if expired:
            self._rewrite()

    def _rewrite(self) -> None:
        body = "".join(f"{uid} {ts:.0f}\n" for uid, ts in self._seen.items())
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            logger.warning("Telegram update receipts not compacted: %s", self._path, exc_info=True)

    def admit(self, update_id: int) -> bool:
        """Record ``update_id``; return False if it was already admitted."""
        if not self._loaded:
            self._load()
        if update_id in self._seen:
            return False
        now = time.time()
        self._seen[update_id] = now
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(f"{update_id} {now:.0f}\n")
        except OSError:
            logger.warning("Telegram update receipt not saved: %s", self._path, exc_info=True)
        return True


def receipts_path(home: Path, bot_token: str) -> Optional[Path]:
    bot_id = (bot_token or "").split(":", 1)[0].strip()
    if not bot_id.isdigit():
        return None
    return Path(home) / f"telegram_update_receipts_{bot_id}.txt"
