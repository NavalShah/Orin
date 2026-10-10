"use client";

import { useId, useMemo } from "react";
import type { IRSummary, LossItem, ScoreNote } from "@/lib/api";
import { clock, humanize } from "@/lib/format";
import css from "./Studio.module.css";

const W = 640;
const H = 210;
const PAD = { left: 46, right: 8, top: 14, bottom: 22 };

/**
 * Performed pitch against written pitch (spec 11.4): the contour as a line, the written
 * notes as bars, loss items as triangles. Small and dependency-free; the sentence under it
 * says the same thing for screen readers.
 */
export function ContourPlot({
  ir,
  notes,
  items,
  selectedNoteId,
  onSelectNote,
}: {
  ir: Pick<IRSummary, "contour" | "duration_s">;
  notes: readonly ScoreNote[];
  items: readonly LossItem[];
  selectedNoteId?: string | null;
  onSelectNote?: (noteId: string) => void;
}) {
  const titleId = useId();
  const summaryId = useId();

  const plot = useMemo(() => {
    const t = ir.contour.t_s;
    const c = ir.contour.cents;
    let lo = Infinity;
    let hi = -Infinity;
    let end = ir.duration_s || 0;
    let points = 0;
    for (let i = 0; i < t.length; i++) {
      const v = c[i];
      if (v == null || !Number.isFinite(v)) continue;
      points++;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
      end = Math.max(end, t[i]);
    }
    for (const n of notes) {
      lo = Math.min(lo, n.cents);
      hi = Math.max(hi, n.cents);
      end = Math.max(end, n.onset_s + n.duration_s);
    }
    if (!Number.isFinite(lo) || !(end > 0)) return null;
    const low = Math.floor((lo - 60) / 100) * 100;
    const high = Math.ceil((hi + 60) / 100) * 100;
    const span = Math.max(high - low, 100);
    const x = (s: number) => PAD.left + (Math.min(Math.max(s, 0), end) / end) * (W - PAD.left - PAD.right);
    const y = (cents: number) => PAD.top + ((high - cents) / span) * (H - PAD.top - PAD.bottom);

    let path = "";
    let pen = false;
    for (let i = 0; i < t.length; i++) {
      const v = c[i];
      if (v == null || !Number.isFinite(v)) {
        pen = false;
        continue;
      }
      path += `${pen ? "L" : "M"}${x(t[i]).toFixed(1)} ${y(v).toFixed(1)}`;
      pen = true;
    }

    const step = span <= 1400 ? 100 : span <= 2800 ? 200 : 600;
    const grid: number[] = [];
    for (let v = Math.ceil(low / step) * step; v <= high; v += step) grid.push(v);
    const labelEvery = grid.length > 10 ? 2 : 1;
    return { lo, hi, end, x, y, path, grid, labelEvery, points };
  }, [ir, notes]);

  if (!plot) return null;
  const markers = items.filter((item) => item.time_s != null);
  const summary =
    `Pitch over ${clock(plot.end)}, in cents from the tonic. ` +
    (plot.points > 0
      ? `The performed pitch moves between ${Math.round(plot.lo)} and ${Math.round(plot.hi)} cents. `
      : "There is no performed contour for typed notes. ") +
    `${notes.length} written ${notes.length === 1 ? "note is" : "notes are"} drawn as bars. ` +
    (markers.length > 0
      ? `${markers.length} loss ${markers.length === 1 ? "item is" : "items are"} marked with triangles along the top.`
      : "No loss items are marked.");

  return (
    <section className="o-panel o-stack" aria-labelledby={titleId} data-noprint>
      <h2 id={titleId}>Performed and written pitch</h2>
      <svg viewBox={`0 0 ${W} ${H}`} className={css.plot} role="img" aria-labelledby={titleId} aria-describedby={summaryId}>
        {plot.grid.map((v, i) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={plot.y(v)} y2={plot.y(v)} className={v % 1200 === 0 ? css.plotOctave : css.plotGrid} />
            {i % plot.labelEvery === 0 ? (
              <text x={PAD.left - 6} y={plot.y(v) + 3.5} textAnchor="end" className={css.plotLabel}>
                {v}
              </text>
            ) : null}
          </g>
        ))}
        <text x={PAD.left} y={H - 6} className={css.plotLabel}>
          0:00
        </text>
        <text x={W - PAD.right} y={H - 6} textAnchor="end" className={css.plotLabel}>
          {clock(plot.end)}
        </text>
        {notes.map((n) => {
          const x0 = plot.x(n.onset_s);
          const width = Math.max(2, plot.x(n.onset_s + n.duration_s) - x0);
          const selected = n.id === selectedNoteId;
          return (
            <rect
              key={n.id}
              x={x0}
              y={plot.y(n.cents) - (selected ? 4 : 2.5)}
              width={width}
              height={selected ? 8 : 5}
              rx={1.5}
              className={selected ? css.plotNoteSelected : css.plotNote}
              onClick={onSelectNote ? () => onSelectNote(n.id) : undefined}
            >
              <title>{`${n.name}, ${Math.round(n.cents)} cents, at ${clock(n.onset_s)}`}</title>
            </rect>
          );
        })}
        {plot.path ? <path d={plot.path} className={css.plotContour} /> : null}
        {markers.map((item, i) => {
          const mx = plot.x(item.time_s as number);
          return (
            <path key={i} d={`M${mx - 4} 2L${mx + 4} 2L${mx} 10Z`} className={css.plotMarker} data-severity={item.severity}>
              <title>{`${humanize(item.code)} at ${clock(item.time_s)}: ${item.human_text}`}</title>
            </path>
          );
        })}
      </svg>
      <p className={css.plotKey} aria-hidden="true">
        <span>
          <svg viewBox="0 0 24 10" width="24" height="10">
            <path d="M1 8C6 0 10 10 14 4S20 2 23 5" className={css.plotContour} />
          </svg>{" "}
          performed
        </span>
        <span>
          <svg viewBox="0 0 24 10" width="24" height="10">
            <rect x="2" y="3" width="20" height="5" rx="1.5" className={css.plotNote} />
          </svg>{" "}
          written
        </span>
        <span>
          <svg viewBox="0 0 12 10" width="12" height="10">
            <path d="M2 1L10 1L6 9Z" className={css.plotMarker} />
          </svg>{" "}
          loss item
        </span>
      </p>
      <p id={summaryId} className="o-muted">
        {summary}
      </p>
    </section>
  );
}
