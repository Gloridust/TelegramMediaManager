"""Settings: proxy (mihomo subscription or external), and the optional bot."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Keys, MihomoConfig
from app.core.services import Services
from app.api.deps import current_user, get_services

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(current_user)])


class ProxyBody(BaseModel):
    mode: str = "off"          # off | external
    type: str = "socks5"       # socks5 | http
    host: str | None = None
    port: int | None = None


class SubscriptionBody(BaseModel):
    url: str


class NodeBody(BaseModel):
    name: str


class BotBody(BaseModel):
    enabled: bool
    token: str | None = None
    admin_id: int | None = None


@router.get("")
async def get_settings(services: Services = Depends(get_services)):
    store = services.store
    token = await store.get_setting(Keys.BOT_TOKEN)
    return {
        "proxy": {
            "mode": await store.get_setting(Keys.PROXY_MODE, "off"),
            "type": await store.get_setting(Keys.PROXY_TYPE, "socks5"),
            "host": await store.get_setting(Keys.PROXY_HOST),
            "port": await store.get_setting(Keys.PROXY_PORT),
            "subscription_set": bool(await store.get_setting(Keys.SUBSCRIPTION_URL)),
            "selected": await store.get_setting(Keys.PROXY_SELECTED),
            "mihomo_enabled": MihomoConfig.ENABLED,
            "mihomo_reachable": await services.proxy.is_reachable(),
        },
        "bot": {
            "enabled": (await store.get_setting(Keys.BOT_ENABLED)) == "1",
            "token_set": bool(token),
            "admin_id": await store.get_setting(Keys.ADMIN_ID),
            "running": services.bot is not None,
        },
        "concurrency": services.engine.max_concurrent,
        "root_path": services.engine.root_path,
    }


async def _reconnect_note(services):
    try:
        await services.reconnect_user()
        return "代理已应用，用户端已按新设置重连。"
    except Exception as e:
        return f"设置已保存，但重连失败：{e}"


@router.post("/proxy")
async def set_proxy(body: ProxyBody, services: Services = Depends(get_services)):
    store = services.store
    await store.set_setting(Keys.PROXY_MODE, body.mode)
    await store.set_setting(Keys.PROXY_TYPE, body.type)
    if body.host is not None:
        await store.set_setting(Keys.PROXY_HOST, body.host)
    if body.port is not None:
        await store.set_setting(Keys.PROXY_PORT, body.port)
    note = await _reconnect_note(services)
    return {"ok": True, "note": note}


@router.post("/subscription")
async def set_subscription(body: SubscriptionBody, services: Services = Depends(get_services)):
    ok, msg = await services.proxy.apply_subscription(body.url.strip())
    if ok:
        # Route the app through the mihomo sidecar.
        await services.store.set_setting(Keys.PROXY_MODE, "mihomo")
        await services.store.set_setting(Keys.PROXY_HOST, MihomoConfig.PROXY_HOST)
        await services.store.set_setting(Keys.PROXY_PORT, MihomoConfig.PROXY_PORT)
        note = await _reconnect_note(services)
        return {"ok": True, "note": f"{msg}。{note}"}
    return {"ok": False, "error": msg}


@router.get("/proxy/nodes")
async def proxy_nodes(services: Services = Depends(get_services)):
    return await services.proxy.list_nodes()


@router.post("/proxy/select")
async def proxy_select(body: NodeBody, services: Services = Depends(get_services)):
    return await services.proxy.select_node(body.name)


@router.post("/proxy/test")
async def proxy_test(body: NodeBody, services: Services = Depends(get_services)):
    return await services.proxy.test_node(body.name)


@router.post("/proxy/refresh")
async def proxy_refresh(services: Services = Depends(get_services)):
    return await services.proxy.refresh_subscription()


@router.post("/bot")
async def set_bot(body: BotBody, services: Services = Depends(get_services)):
    store = services.store
    if body.token is not None:
        await store.set_setting(Keys.BOT_TOKEN, body.token)
    if body.admin_id is not None:
        await store.set_setting(Keys.ADMIN_ID, body.admin_id)
    await store.set_setting(Keys.BOT_ENABLED, "1" if body.enabled else "0")

    if body.enabled:
        await services.stop_bot()  # restart cleanly to pick up new token/admin
        await services.maybe_start_bot()
        running = services.bot is not None
        return {"ok": True, "running": running,
                "note": "机器人已启动。" if running else "已启用，但缺少令牌或凭据，未能启动。"}
    await services.stop_bot()
    return {"ok": True, "running": False, "note": "机器人已停用。"}
