from __future__ import annotations

from .base import BaseRepository


class NotificationRepository(BaseRepository):
    def fetch_notifications(self, limit: int = 200):
        return self.fetchall(
            """
            SELECT *
            FROM notifications
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
