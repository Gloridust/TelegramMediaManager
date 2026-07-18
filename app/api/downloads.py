"""Download control: submit links/channels, monitor tasks, cancel, resume, retry."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.services import Services
from app.api.deps import current_user, get_services

router = APIRouter(prefix="/api/downloads", tags=["downloads"], dependencies=[Depends(current_user)])


class LinkBody(BaseModel):
    link: str
    folder: str | None = None


class ConcurrencyBody(BaseModel):
    value: int


@router.post("/link")
async def download_link(body: LinkBody, services: Services = Depends(get_services)):
    return await services.engine.download_link(body.link.strip(), folder=body.folder)


@router.post("/channel")
async def download_channel(body: LinkBody, services: Services = Depends(get_services)):
    return await services.engine.start_channel(body.link.strip(), folder=body.folder)


@router.post("/cancel")
async def cancel(services: Services = Depends(get_services)):
    return await services.engine.cancel_all()


@router.post("/resume")
async def resume(services: Services = Depends(get_services)):
    return await services.engine.resume()


@router.post("/retry-failed")
async def retry_failed(services: Services = Depends(get_services)):
    n = await services.store.requeue_failed()
    if n:
        await services.engine.resume()
    return {"ok": True, "requeued": n}


@router.post("/clear-history")
async def clear_history(services: Services = Depends(get_services)):
    n = await services.store.clear_history()
    return {"ok": True, "cleared": n}


@router.post("/concurrency")
async def set_concurrency(body: ConcurrencyBody, services: Services = Depends(get_services)):
    n = await services.engine.set_concurrency(body.value)
    return {"ok": True, "value": n}


@router.get("/tasks")
async def tasks(services: Services = Depends(get_services)):
    engine = services.engine
    counts = await services.store.counts()
    channel_running = bool(engine.channel_task and not engine.channel_task.done())
    return {
        "active": engine.active_count,
        "queued": engine.queue_size,
        "concurrency": engine.max_concurrent,
        "counts": counts,
        "channel_running": channel_running,
        "recent": await services.store.recent_jobs(60),
        "pending": await services.store.pending_jobs(),
        "failed": await services.store.failed_jobs(),
        "channels": await services.store.running_channels(),
        "root_missing": engine.root_missing,
    }
