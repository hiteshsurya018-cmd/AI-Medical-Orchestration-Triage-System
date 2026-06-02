from __future__ import annotations

from .base import BaseRepository


class AuthRepository(BaseRepository):
    def fetch_user_by_email(self, email: str):
        return self.fetchone("SELECT * FROM users WHERE email = :email", {"email": email})

    def fetch_user_by_id(self, user_id: int):
        return self.fetchone("SELECT * FROM users WHERE id = :user_id", {"user_id": user_id})
