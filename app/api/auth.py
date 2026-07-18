"""Authentication, first-run setup wizard and account management."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.config import Keys, WebConfig
from app.core.i18n import SUPPORTED, tr
from app.core.security import hash_password, new_token, verify_password
from app.core.services import Services
from app.api.deps import (clear_session_cookie, current_user, get_services,
                          set_session_cookie, throttle)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SetupBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    api_id: int | None = None
    api_hash: str | None = None
    lang: str | None = None


class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


def _client_ip(request: Request) -> str:
    # Honour a single reverse-proxy hop if present, else the socket peer.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


@router.get("/status")
async def auth_status(request: Request, services: Services = Depends(get_services)):
    """Bootstrap state for the frontend: is setup needed, are we logged in."""
    setup_needed = not await services.store.has_admin()
    authenticated = False
    token = request.cookies.get("tmm_session")
    if token and await services.store.get_session(token):
        authenticated = True
    return {"setup_needed": setup_needed, "authenticated": authenticated}


@router.post("/setup")
async def setup(body: SetupBody, response: Response, services: Services = Depends(get_services)):
    """Create the admin account on first run. Refuses once one exists."""
    if await services.store.has_admin():
        raise HTTPException(status.HTTP_409_CONFLICT, await tr(services.store, "already_setup"))
    if body.lang in SUPPORTED:
        await services.store.set_setting(Keys.UI_LANG, body.lang)
    user_id = await services.store.create_user(body.username, hash_password(body.password))

    if body.api_id and body.api_hash:
        await services.store.set_setting(Keys.API_ID, body.api_id)
        await services.store.set_setting(Keys.API_HASH, body.api_hash)
        try:
            await services.reconnect_user()
        except Exception:
            pass

    token = new_token()
    await services.store.create_session(token, user_id, WebConfig.SESSION_TTL)
    await services.store.touch_login(user_id)
    set_session_cookie(response, token)
    return {"ok": True}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response,
                services: Services = Depends(get_services)):
    ip = _client_ip(request)
    if not throttle.check(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, await tr(services.store, "too_many_attempts"))

    user = await services.store.get_user(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        throttle.record(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, await tr(services.store, "bad_credentials"))

    throttle.reset(ip)
    token = new_token()
    await services.store.create_session(token, user["id"], WebConfig.SESSION_TTL,
                                        user_agent=request.headers.get("user-agent", ""))
    await services.store.touch_login(user["id"])
    set_session_cookie(response, token)
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, services: Services = Depends(get_services)):
    token = request.cookies.get("tmm_session")
    if token:
        await services.store.delete_session(token)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user=Depends(current_user)):
    return {"username": user["username"], "last_login": user["last_login"]}


@router.post("/password")
async def change_password(body: PasswordBody, services: Services = Depends(get_services),
                          user=Depends(current_user)):
    if not verify_password(body.old_password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, await tr(services.store, "wrong_current_password"))
    await services.store.set_password(user["id"], hash_password(body.new_password))
    # Invalidate every session so other devices must re-authenticate.
    await services.store.delete_user_sessions(user["id"])
    return {"ok": True}
