"""
Supabase client singleton for backend services.
Uses the Service Role Key so the background worker can read/write across profiles, jobs, and storage.
Lazily imports the `supabase` SDK so unit tests can run without top-level namespace collisions
against the local `/supabase` migrations folder.
"""
from typing import Any, Optional
from backend.core.config import settings

_supabase_client: Optional[Any] = None


def get_supabase_client() -> Any:
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client

        _supabase_client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,
        )
    return _supabase_client
