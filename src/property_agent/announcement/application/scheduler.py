"""Persistent scheduled-announcement executor.

The scheduler owns no business state. It periodically asks AnnouncementService to
publish announcements whose manager-authorized schedule is due in PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging

from property_agent.announcement.application.service import AnnouncementService

logger = logging.getLogger(__name__)


class AnnouncementScheduler:
    def __init__(self, service: AnnouncementService, *, interval_seconds: float = 15.0) -> None:
        self._service = service
        self._interval_seconds = interval_seconds
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                published = await asyncio.to_thread(self._service.publish_due)
                if published:
                    logger.info("Published %s scheduled announcement(s)", published)
            except Exception:
                logger.exception("Scheduled announcement scan failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stopped.set()
