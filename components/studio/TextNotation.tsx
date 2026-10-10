"use client";

import { useEffect, useMemo, useRef } from "react";
import type { OutputFile } from "@/lib/api";
import css from "./Studio.module.css";

type Token = NonNullable<OutputFile["tokens"]>[number];

/** Split text into plain runs and note tokens. Overlapping or out-of-range tokens stay plain. */
export function splitTokens(text: string, tokens: readonly Token[]): Array<{ text: string; noteId?: string }> {
  const sorted = [...tokens].filter((t) => t.end > t.start).sort((a, b) => a.start - b.start);
  const parts: Array<{ text: string; noteId?: string }> = [];
  let pos = 0;
  for (const t of sorted) {
    if (t.start < pos || t.end > text.length) continue;
    if (t.start > pos) parts.push({ text: text.slice(pos, t.start) });
    parts.push({ text: text.slice(t.start, t.end), noteId: t.note_id });
    pos = t.end;
  }
  if (pos < text.length) parts.push({ text: text.slice(pos) });
  return parts;
}

/** Scroll only the notation's own container so the note is in view; the page stays where it is. */
export function reveal(container: HTMLElement, el: Element): void {
  const c = container.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  if (r.left < c.left) container.scrollLeft -= c.left - r.left + 24;
  else if (r.right > c.right) container.scrollLeft += r.right - c.right + 24;
  if (r.top < c.top) container.scrollTop -= c.top - r.top + 24;
  else if (r.bottom > c.bottom) container.scrollTop += r.bottom - c.bottom + 24;
}

/**
 * A text notation (sargam, note names, ABC, tab) with each note wrapped so it can be
 * highlighted. A low-confidence note gets a dashed outline, a "?" marker and hidden text for
 * screen readers: never colour alone (spec 6.5, 13.5).
 */
export function TextNotation({
  text,
  tokens,
  lowIds,
  currentId = null,
  selectedId = null,
  onSelect,
  label,
}: {
  text: string;
  tokens: readonly Token[];
  lowIds: ReadonlySet<string>;
  currentId?: string | null;
  selectedId?: string | null;
  onSelect?: (noteId: string) => void;
  label: string;
}) {
  const ref = useRef<HTMLPreElement>(null);
  const parts = useMemo(() => splitTokens(text, tokens), [text, tokens]);

  useEffect(() => {
    if (!currentId || !ref.current) return;
    for (const el of Array.from(ref.current.querySelectorAll<HTMLElement>("[data-note-id]"))) {
      if (el.dataset.noteId === currentId) {
        reveal(ref.current, el);
        break;
      }
    }
  }, [currentId]);

  return (
    <pre ref={ref} className={css.textNotation} aria-label={label} tabIndex={0}>
      {parts.map((part, i) => {
        if (!part.noteId) return <span key={i}>{part.text}</span>;
        const id = part.noteId;
        const low = lowIds.has(id);
        const classes = [css.note];
        if (low) classes.push(css.noteLow);
        if (id === selectedId) classes.push(css.noteSelected);
        if (id === currentId) classes.push(css.noteCurrent);
        return (
          <span
            key={i}
            className={classes.join(" ")}
            data-note-id={id}
            data-low={low ? "true" : undefined}
            data-current={id === currentId ? "true" : undefined}
            data-selected={id === selectedId ? "true" : undefined}
            onClick={onSelect ? () => onSelect(id) : undefined}
          >
            {part.text}
            {low ? (
              <>
                <span className={css.lowMark} aria-hidden="true">
                  ?
                </span>
                <span className="o-sr-only"> (low confidence)</span>
              </>
            ) : null}
          </span>
        );
      })}
    </pre>
  );
}
