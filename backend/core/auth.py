"""Validate access tokens with Supabase; never trust caller-supplied ownership."""
from fastapi import Header, HTTPException
from backend.core.database import get_supabase_client


def current_user(authorization: str = Header(default='')) -> str:
    if not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Sign in to continue.')
    try:
        response = get_supabase_client().auth.get_user(authorization[7:])
        if not response.user:
            raise ValueError('Missing user')
        return str(response.user.id)
    except Exception:
        raise HTTPException(401, 'Your session has expired. Sign in again.') from None
