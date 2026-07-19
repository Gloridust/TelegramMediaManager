"""File manager: browse folders and files, create/rename/delete, download, and
choose the download directory.

All paths are constrained to the working root; traversal outside it (or deleting
the root itself) is refused by the engine guards.
"""

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.i18n import tr
from app.core.services import Services
from app.api.deps import current_user, get_services

router = APIRouter(prefix="/api/files", tags=["files"], dependencies=[Depends(current_user)])


class PathBody(BaseModel):
    path: str


class MkdirBody(BaseModel):
    parent: str
    name: str


class RenameBody(BaseModel):
    path: str
    name: str


class RootBody(BaseModel):
    path: str


def _view(engine, target, folders, files):
    return {
        "root": engine.root_path,
        "path": target,
        "rel": engine._rel(target),
        "is_root": engine._is_root(target),
        "current_dir": engine.current_dir,
        "current_rel": engine._rel(engine.current_dir),
        "is_current": os.path.realpath(target) == os.path.realpath(engine.current_dir),
        "parent": None if engine._is_root(target) else os.path.dirname(target),
        "folders": [{"name": n, "path": os.path.join(target, n)} for n in folders],
        "files": [{"name": n, "path": os.path.join(target, n), "size": sz} for n, sz in files],
    }


@router.get("")
async def browse(path: str | None = None, services: Services = Depends(get_services)):
    engine = services.engine
    target, folders, files = engine.list_entries(path or engine.current_dir)
    return _view(engine, target, folders, files)


@router.get("/download")
async def download(path: str, inline: bool = False, services: Services = Depends(get_services)):
    engine = services.engine
    p = engine.resolve_path(path, must_be="file")
    if not p:
        raise HTTPException(404, await tr(services.store, "file_not_found"))
    return FileResponse(p, filename=os.path.basename(p),
                        content_disposition_type="inline" if inline else "attachment")


@router.post("/mkdir")
async def mkdir(body: MkdirBody, services: Services = Depends(get_services)):
    engine = services.engine
    path = engine.make_dir(body.parent, body.name)
    if not path:
        raise HTTPException(400, await tr(services.store, "invalid_folder"))
    await engine.set_current_dir(path)
    target, folders, files = engine.list_entries(path)
    return _view(engine, target, folders, files)


@router.post("/rename")
async def rename(body: RenameBody, services: Services = Depends(get_services)):
    engine = services.engine
    if not engine.rename_path(body.path, body.name):
        raise HTTPException(400, await tr(services.store, "rename_failed"))
    return {"ok": True}


@router.post("/delete")
async def delete(body: PathBody, services: Services = Depends(get_services)):
    engine = services.engine
    if not engine.delete_path(body.path):
        raise HTTPException(400, await tr(services.store, "delete_failed"))
    # If the current download dir was deleted, fall back to the root.
    if not os.path.isdir(engine.current_dir):
        await engine.set_current_dir(engine.root_path)
    return {"ok": True}


@router.post("/set-current")
async def set_current(body: PathBody, services: Services = Depends(get_services)):
    engine = services.engine
    if not await engine.set_current_dir(body.path):
        raise HTTPException(400, await tr(services.store, "invalid_path"))
    return {"ok": True, "current_rel": engine._rel(engine.current_dir)}


@router.post("/set-root")
async def set_root(body: RootBody, services: Services = Depends(get_services)):
    engine = services.engine
    try:
        await engine.set_root(body.path)
    except OSError as e:
        raise HTTPException(400, await tr(services.store, "invalid_path_detail", e=e))
    pending = await services.store.pending_jobs()
    return {"ok": True, "root": engine.root_path, "pending": len(pending)}
