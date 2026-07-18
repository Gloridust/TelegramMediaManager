"""Telegram account: API credentials, QR login, 2FA, status, logout."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.config import Keys
from app.core.i18n import tr
from app.core.services import Services
from app.api.deps import current_user, get_services

router = APIRouter(prefix="/api/telegram", tags=["telegram"], dependencies=[Depends(current_user)])


class CredentialsBody(BaseModel):
    api_id: int
    api_hash: str


class PasswordBody(BaseModel):
    password: str


@router.get("/status")
async def status(services: Services = Depends(get_services)):
    tg = services.tg
    return {
        "credentials_ready": await tg.credentials_ready(),
        "authorized": await tg.is_authorized(),
        "me": await tg.me(),
        "login": tg.login_status(),
    }


@router.post("/credentials")
async def set_credentials(body: CredentialsBody, services: Services = Depends(get_services)):
    await services.store.set_setting(Keys.API_ID, body.api_id)
    await services.store.set_setting(Keys.API_HASH, body.api_hash)
    try:
        await services.reconnect_user()
    except Exception as e:
        raise HTTPException(400, await tr(services.store, "creds_saved_connect_failed", e=e))
    return {"ok": True, "authorized": await services.tg.is_authorized()}


@router.post("/login/start")
async def login_start(services: Services = Depends(get_services)):
    if not await services.tg.credentials_ready():
        raise HTTPException(400, await tr(services.store, "need_api_creds"))
    try:
        return await services.tg.start_login()
    except Exception as e:
        raise HTTPException(400, await tr(services.store, "login_start_failed", e=e))


@router.get("/login/status")
async def login_status(services: Services = Depends(get_services)):
    st = services.tg.login_status()
    # On first observation of success, resume any unfinished work.
    if st["state"] == "success" and await services.tg.is_authorized():
        await services.engine.resume()
        await services.maybe_start_bot()
    return st


@router.get("/login/qr.png")
async def login_qr(services: Services = Depends(get_services)):
    png = services.tg.qr_png()
    if not png:
        raise HTTPException(404, await tr(services.store, "no_qr"))
    return Response(content=png, media_type="image/png")


@router.post("/login/password")
async def login_password(body: PasswordBody, services: Services = Depends(get_services)):
    ok = await services.tg.submit_password(body.password)
    if not ok:
        raise HTTPException(400, await tr(services.store, "no_password_pending"))
    return {"ok": True}


@router.post("/logout")
async def logout(services: Services = Depends(get_services)):
    await services.tg.logout()
    return {"ok": True}
