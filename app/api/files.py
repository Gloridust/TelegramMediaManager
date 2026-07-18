"""Folder browser: navigate subfolders, create folders, choose the download dir.

All paths are constrained to the working root; traversal outside it is refused by
the engine's ``_is_within_root`` guard.
"""

import os

from fastapi import APIRouter, Depends, HTTPException
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


class RootBody(BaseModel):
    path: str


def _view(engine, target, names):
    return {
        "root": engine.root_path,
        "path": target,
        "rel": engine._rel(target),
        "is_root": engine._is_root(target),
        "current_dir": engine.current_dir,
        "current_rel": engine._rel(engine.current_dir),
        "is_current": os.path.realpath(target) == os.path.realpath(engine.current_dir),
        "parent": None if engine._is_root(target) else os.path.dirname(target),
        "entries": [{"name": n, "path": os.path.join(target, n)} for n in names],
    }


@router.get("")
async def browse(path: str | None = None, services: Services = Depends(get_services)):
    engine = services.engine
    target, names = engine.list_dir(path or engine.current_dir)
    return _view(engine, target, names)


@router.post("/mkdir")
async def mkdir(body: MkdirBody, services: Services = Depends(get_services)):
    engine = services.engine
    path = engine.make_dir(body.parent, body.name)
    if not path:
        raise HTTPException(400, await tr(services.store, "invalid_folder"))
    await engine.set_current_dir(path)
    target, names = engine.list_dir(path)
    return _view(engine, target, names)


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
