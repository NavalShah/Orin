// A project's clip layout as a few rectangles, for the "Previous sessions" thumbnails.
import { clipEnd, projectEndSec, type Project, type TrackKind } from "./project";

export type ThumbRect = { x: number; y: number; width: number; height: number; kind: TrackKind };

export function thumbnailRects(project: Project, width: number, height: number): ThumbRect[] {
  const rows = Math.max(1, project.tracks.length);
  const rowH = height / rows;
  const end = Math.max(projectEndSec(project), 1);
  const scale = width / end;
  const rects: ThumbRect[] = [];
  project.tracks.forEach((track, row) => {
    const y = row * rowH + rowH * 0.15;
    const h = Math.max(1, rowH * 0.7);
    if (track.kind === "drums") {
      // The pattern fills the whole length; show it as one long bar.
      if (project.drums?.rows.some((r) => r.steps.some(Boolean))) rects.push({ x: 0, y, width, height: h, kind: "drums" });
      return;
    }
    for (const clip of project.clips) {
      if (clip.trackId !== track.id) continue;
      rects.push({
        x: clip.startSec * scale,
        y,
        width: Math.max(1, (clipEnd(clip) - clip.startSec) * scale),
        height: h,
        kind: track.kind,
      });
    }
  });
  return rects;
}
