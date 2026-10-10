"""Workspace projects: list, create, read, save, duplicate, delete (spec 12.7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import schemas
from ..db import UserRow
from ..deps import AppCtx, current_user, get_ctx
from ..services import projects as project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[schemas.ProjectSummary])
def list_projects(user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> list[schemas.ProjectSummary]:
    return project_service.list_projects(ctx, user.id)


@router.post("", response_model=schemas.ProjectOut)
def create_project(body: schemas.ProjectSave, user: UserRow = Depends(current_user),
                   ctx: AppCtx = Depends(get_ctx)) -> schemas.ProjectOut:
    return project_service.create(ctx, user.id, body)


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.ProjectOut:
    return project_service.get(ctx, user.id, project_id)


@router.put("/{project_id}", response_model=schemas.ProjectOut)
def save_project(project_id: str, body: schemas.ProjectSave, user: UserRow = Depends(current_user),
                 ctx: AppCtx = Depends(get_ctx)) -> schemas.ProjectOut:
    return project_service.save(ctx, user.id, project_id, body)


@router.post("/{project_id}/duplicate", response_model=schemas.ProjectOut)
def duplicate_project(project_id: str, user: UserRow = Depends(current_user),
                      ctx: AppCtx = Depends(get_ctx)) -> schemas.ProjectOut:
    return project_service.duplicate(ctx, user.id, project_id)


@router.delete("/{project_id}", response_model=schemas.Ok)
def delete_project(project_id: str, user: UserRow = Depends(current_user), ctx: AppCtx = Depends(get_ctx)) -> schemas.Ok:
    project_service.delete(ctx, user.id, project_id)
    return schemas.Ok()
