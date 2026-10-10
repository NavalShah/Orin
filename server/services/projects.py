"""Workspace projects (spec 12.7). The project JSON is the Workspace's own versioned
document and is opaque here; only the list view peeks inside, and it tolerates any shape."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select

from .. import schemas
from ..db import ProjectRow, dumps, loads, new_id, now_iso, owned
from ..deps import AppCtx
from ..errors import not_found


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _first(data: dict, *keys: str) -> Optional[float]:
    for key in keys:
        found = _number(data.get(key))
        if found is not None:
            return found
    return None


def summarise(project: Any) -> dict[str, Any]:
    """track_count, clip_count, duration_s and tempo from whatever the project holds."""
    project = project if isinstance(project, dict) else {}
    tracks = project.get("tracks") if isinstance(project.get("tracks"), list) else []
    clips = list(project.get("clips")) if isinstance(project.get("clips"), list) else []
    for track in tracks:  # some layouts nest clips inside their track
        if isinstance(track, dict) and isinstance(track.get("clips"), list):
            clips += track["clips"]
    end = 0.0
    for clip in clips:
        if isinstance(clip, dict):
            start = _first(clip, "startSec", "start_s", "start") or 0.0
            length = _first(clip, "lengthSec", "length_s", "length", "duration_s", "duration") or 0.0
            end = max(end, start + length)
    duration = _first(project, "durationSec", "duration_s", "duration")
    return {
        "track_count": len(tracks), "clip_count": len(clips),
        "duration_s": round(duration if duration is not None else end, 3),
        "tempo": _first(project, "tempo", "tempo_bpm", "bpm"),
    }


def _out(row: ProjectRow) -> schemas.ProjectOut:
    return schemas.ProjectOut(id=row.id, name=row.name, project=loads(row.project_json, {}), version=row.version,
                              created_at=row.created_at, updated_at=row.updated_at)


def list_projects(ctx: AppCtx, user_id: str) -> list[schemas.ProjectSummary]:
    with ctx.db.session() as s:
        rows = s.execute(select(ProjectRow).where(ProjectRow.user_id == user_id)
                         .order_by(ProjectRow.updated_at.desc())).scalars().all()
    return [schemas.ProjectSummary(id=r.id, name=r.name, version=r.version, updated_at=r.updated_at,
                                   **summarise(loads(r.project_json, {}))) for r in rows]


def create(ctx: AppCtx, user_id: str, body: schemas.ProjectSave) -> schemas.ProjectOut:
    now = now_iso()
    row = ProjectRow(id=new_id(), user_id=user_id, name=body.name.strip() or "Untitled", project_json=dumps(body.project),
                     version=body.version, created_at=now, updated_at=now)
    with ctx.db.session() as s:
        s.add(row)
    return _out(row)


def get(ctx: AppCtx, user_id: str, project_id: str) -> schemas.ProjectOut:
    with ctx.db.session() as s:
        row = owned(s, ProjectRow, user_id, project_id)
    if row is None:
        raise not_found("That project")
    return _out(row)


def save(ctx: AppCtx, user_id: str, project_id: str, body: schemas.ProjectSave) -> schemas.ProjectOut:
    with ctx.db.session() as s:
        row = owned(s, ProjectRow, user_id, project_id)
        if row is None:
            raise not_found("That project")
        row.name = body.name.strip() or row.name
        row.project_json = dumps(body.project)
        row.version = body.version
        row.updated_at = now_iso()
    return _out(row)


def duplicate(ctx: AppCtx, user_id: str, project_id: str) -> schemas.ProjectOut:
    now = now_iso()
    with ctx.db.session() as s:
        row = owned(s, ProjectRow, user_id, project_id)
        if row is None:
            raise not_found("That project")
        copy = ProjectRow(id=new_id(), user_id=user_id, name=f"{row.name} (copy)"[:120], project_json=row.project_json,
                          version=row.version, created_at=now, updated_at=now)
        s.add(copy)
    return _out(copy)


def delete(ctx: AppCtx, user_id: str, project_id: str) -> None:
    with ctx.db.session() as s:
        row = owned(s, ProjectRow, user_id, project_id)
        if row is None:
            raise not_found("That project")
        s.delete(row)
