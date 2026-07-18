"""Shared FastAPI dependencies: service access, session auth and login throttling."""

import time

from fastapi import Depends, HTTPException, Request, status

from app.config import WebConfig
from app.core.i18n import tr
from app.core.services import Services

COOKIE_NAME = "tmm_session"


def get_services(request: Request) -> Services:
    return request.app.state.services


async def current_user(request: Request, services: Services = Depends(get_services)):
    """Resolve the logged-in user from the session cookie, or 401."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, await tr(services.store, "not_logged_in"))
    session = await services.store.get_session(token)
    if not session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, await tr(services.store, "session_expired"))
    user = await services.store.get_user_by_id(session["user_id"])
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, await tr(services.store, "user_missing"))
    return user


def set_session_cookie(response, token):
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=WebConfig.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=WebConfig.SECURE_COOKIE,
    )


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME)


class LoginThrottle:
    """In-memory per-IP login attempt limiter. Enough to blunt brute force on a
    self-hosted panel; not a substitute for keeping the panel off the open
    internet."""

    def __init__(self):
        self._hits: dict[str, list[float]] = {}

    def check(self, ip: str) -> bool:
        now = time.time()
        window = WebConfig.LOGIN_WINDOW
        hits = [t for t in self._hits.get(ip, []) if now - t < window]
        self._hits[ip] = hits
        return len(hits) < WebConfig.LOGIN_MAX_ATTEMPTS

    def record(self, ip: str):
        self._hits.setdefault(ip, []).append(time.time())

    def reset(self, ip: str):
        self._hits.pop(ip, None)


throttle = LoginThrottle()
