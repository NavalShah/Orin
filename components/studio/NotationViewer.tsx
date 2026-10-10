"use client";

import { memo, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { api, type ConversionOut, type OutputFile, type ScoreNote } from "@/lib/api";
import { confidenceText } from "@/lib/format";
import { useScorePlayer } from "@/components/audio/useScorePlayer";
import { Term } from "@/components/shell/Glossary";
import type { Registry } from "@/components/shell/registry";
import { reveal, TextNotation } from "./TextNotation";
import css from "./Studio.module.css";

const SVG_NS = "http://www.w3.org/2000/svg";
const DESCRIBE = "describe";

/** Outputs that can be shown on screen: SVG pages, or plain text. */
export function viewableOutputs(outputs: readonly OutputFile[], primary?: string): OutputFile[] {
  const shown = outputs.filter(
    (o) => o.kind !== DESCRIBE && ((o.pages?.length ?? 0) > 0 || (o.text != null && o.media_type.startsWith("text/"))),
  );
  return [...shown].sort((a, b) => Number(b.kind === primary) - Number(a.kind === primary));
}

/** Score notes whose source event the analysis was unsure of (confidence under 0.5). */
export function lowConfidenceNoteIds(conversion: Pick<ConversionOut, "ir" | "score">): Set<string> {
  const events = new Set(conversion.ir?.low_confidence_event_ids ?? []);
  const ids = new Set<string>();
  for (const n of conversion.score?.notes ?? []) {
    if (n.source_event_id && events.has(n.source_event_id)) ids.add(n.id);
  }
  return ids;
}

const SvgPages = memo(function SvgPages({ pages }: { pages: readonly string[] }) {
  // The SVG strings come from our own server's renderer.
  return (
    <>
      {pages.map((svg, i) => (
        <div key={i} className={css.svgPage} dangerouslySetInnerHTML={{ __html: svg }} />
      ))}
    </>
  );
});

/** Draw a frame around a note inside the SVG; with "low" also a "?" above it. */
function frame(el: SVGGraphicsElement, kind: "low" | "current" | "selected"): boolean {
  try {
    const box = el.getBBox();
    if (!(box.width > 0) && !(box.height > 0)) return false;
    const host = el instanceof SVGGElement ? el : el.parentNode;
    if (!host) return false;
    const unit = Math.max(box.width, box.height, 1);
    const pad = unit * (kind === "low" ? 0.22 : 0.12);
    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("x", String(box.x - pad));
    rect.setAttribute("y", String(box.y - pad));
    rect.setAttribute("width", String(box.width + pad * 2));
    rect.setAttribute("height", String(box.height + pad * 2));
    rect.setAttribute("rx", String(pad));
    rect.setAttribute("fill", "none");
    rect.setAttribute("stroke-width", String(unit * 0.05));
    if (kind === "low") rect.setAttribute("stroke-dasharray", `${unit * 0.14} ${unit * 0.1}`);
    rect.setAttribute("data-orin-frame", kind);
    rect.setAttribute("pointer-events", "none");
    host.appendChild(rect);
    if (kind === "low") {
      const mark = document.createElementNS(SVG_NS, "text");
      mark.setAttribute("x", String(box.x + box.width + pad));
      mark.setAttribute("y", String(box.y - pad));
      mark.setAttribute("font-size", String(unit * 0.6));
      mark.setAttribute("font-weight", "700");
      mark.setAttribute("stroke", "none");
      mark.setAttribute("data-orin-frame", kind);
      mark.setAttribute("pointer-events", "none");
      mark.textContent = "?";
      host.appendChild(mark);
    }
    return true;
  } catch {
    // No layout yet (or a test environment): the CSS outline still marks the note.
    return false;
  }
}

function SvgNotation({
  pages,
  noteIds,
  lowIds,
  currentId,
  selectedId,
  onSelect,
  label,
  describedBy,
}: {
  pages: readonly string[];
  noteIds: ReadonlySet<string>;
  lowIds: ReadonlySet<string>;
  currentId: string | null;
  selectedId: string | null;
  onSelect?: (noteId: string) => void;
  label: string;
  describedBy?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  /** The note's group, plus the extra groups of a note split by a bar line or a tie ("<id>t2", "<id>t3"...). */
  function findAll(id: string): SVGGraphicsElement[] {
    const root = ref.current;
    if (!root) return [];
    const safe = id.replace(/["\\]/g, "\\$&");
    const tied = Array.from(root.querySelectorAll<SVGGraphicsElement>(`[id^="${safe}t"]`)).filter((el) => /^t\d+$/.test(el.id.slice(id.length)));
    const base = root.querySelector<SVGGraphicsElement>(`[id="${safe}"]`);
    return base ? [base, ...tied] : tied;
  }
  function clear(kind: string) {
    const root = ref.current;
    if (!root) return;
    root.querySelectorAll(`[data-orin-frame="${kind}"]`).forEach((el) => el.remove());
    root.querySelectorAll(`[data-orin-${kind}]`).forEach((el) => el.removeAttribute(`data-orin-${kind}`));
  }
  function mark(kind: "low" | "current" | "selected", id: string): SVGGraphicsElement | null {
    const found = findAll(id);
    for (const el of found) el.setAttribute(`data-orin-${kind}`, frame(el, kind) ? "framed" : "plain");
    return found[0] ?? null;
  }

  useEffect(() => {
    clear("low");
    lowIds.forEach((id) => mark("low", id));
  }, [pages, lowIds]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    clear("selected");
    const el = selectedId ? mark("selected", selectedId) : null;
    if (el && ref.current) reveal(ref.current, el);
  }, [pages, selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    clear("current");
    const el = currentId ? mark("current", currentId) : null;
    if (el && ref.current) reveal(ref.current, el);
  }, [pages, currentId]); // eslint-disable-line react-hooks/exhaustive-deps

  function onClick(e: MouseEvent<HTMLDivElement>) {
    if (!onSelect) return;
    let el = e.target as Element | null;
    while (el && el !== ref.current) {
      const id = el.id ? (noteIds.has(el.id) ? el.id : el.id.replace(/t\d+$/, "")) : "";
      if (id && noteIds.has(id)) {
        onSelect(id);
        return;
      }
      el = el.parentElement;
    }
  }

  return (
    <div ref={ref} className={css.svgWrap} role="img" aria-label={label} aria-describedby={describedBy} tabIndex={0} onClick={onClick}>
      <SvgPages pages={pages} />
    </div>
  );
}

function noteAt(notes: readonly ScoreNote[], t: number, key: "onset_s" | "source_onset_s"): string | null {
  let id: string | null = null;
  for (const n of notes) if (n[key] <= t && t < n[key] + n.duration_s) id = n.id;
  return id;
}

/**
 * The centre of the Studio: the notation in each available form, the plain-language
 * description (always shown, as the text alternative), and Original / Notation playback
 * with the sounding note highlighted (spec 11.2, 13.5).
 */
export function NotationViewer({
  conversion,
  registry,
  selectedNoteId,
  onSelectNote,
  tab,
  onTab,
}: {
  conversion: ConversionOut;
  registry: Registry;
  selectedNoteId: string | null;
  onSelectNote: (noteId: string | null) => void;
  tab: string | null;
  onTab: (kind: string) => void;
}) {
  const baseId = useId();
  const outputs = useMemo(() => conversion.outputs ?? [], [conversion.outputs]);
  const target = registry.format(conversion.output_format);
  const shown = useMemo(() => viewableOutputs(outputs, target?.primary_renderer), [outputs, target?.primary_renderer]);
  const describe = outputs.find((o) => o.kind === DESCRIBE);
  const active = shown.find((o) => o.kind === tab) ?? shown[0] ?? null;
  const notes = useMemo(() => conversion.score?.notes ?? [], [conversion.score]);
  const lowIds = useMemo(() => lowConfidenceNoteIds(conversion), [conversion]);
  const noteIds = useMemo(() => new Set(notes.map((n) => n.id)), [notes]);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // --- playback: Original (the upload) or Notation (the synth) ---
  const original = conversion.source_asset && ["audio", "stem"].includes(conversion.source_asset.kind) ? conversion.source_asset : null;
  const [source, setSource] = useState<"original" | "notation">("notation");
  const [originalNote, setOriginalNote] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const player = useScorePlayer(conversion.score ?? null);
  const currentId = source === "notation" ? player.currentId : originalNote;
  const describeId = `${baseId}-describe`;

  function choose(next: "original" | "notation") {
    player.stop();
    audioRef.current?.pause();
    setOriginalNote(null);
    setSource(next);
  }

  function onTabKey(e: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step || shown.length === 0) return;
    e.preventDefault();
    const next = (index + step + shown.length) % shown.length;
    onTab(shown[next].kind);
    tabRefs.current[next]?.focus();
  }

  function step(delta: number) {
    if (notes.length === 0) return;
    const at = notes.findIndex((n) => n.id === selectedNoteId);
    const next = at < 0 ? (delta > 0 ? 0 : notes.length - 1) : Math.min(notes.length - 1, Math.max(0, at + delta));
    onSelectNote(notes[next].id);
  }

  const selected = notes.find((n) => n.id === selectedNoteId) ?? null;
  const selectedIndex = selected ? notes.indexOf(selected) : -1;
  const playingIndex = currentId ? notes.findIndex((n) => n.id === currentId) : -1;
  const rendererLabel = (kind: string) => registry.registry.renderers.find((r) => r.kind === kind)?.label ?? kind;

  return (
    <section className={`o-panel o-stack ${css.viewer}`} aria-labelledby={`${baseId}-title`}>
      <div className="o-row" style={{ justifyContent: "space-between" }}>
        <h2 id={`${baseId}-title`}>Notation: {registry.formatName(conversion.output_format)}</h2>
        {conversion.same_format ? <span className="o-badge o-badge-info">Clean up / quantize</span> : null}
      </div>

      <div className={`o-stack ${css.playback}`} data-noprint>
        <div className="o-row">
          <div className="o-row" role="group" aria-label="Play which version" style={{ gap: 0 }}>
            <button
              type="button"
              className={`o-btn ${css.segLeft}`}
              aria-pressed={source === "original"}
              disabled={!original}
              onClick={() => choose("original")}
            >
              Original
            </button>
            <button type="button" className={`o-btn ${css.segRight}`} aria-pressed={source === "notation"} onClick={() => choose("notation")}>
              Notation
            </button>
          </div>
          {source === "notation" ? (
            <button
              type="button"
              className="o-btn o-btn-primary"
              disabled={notes.length === 0}
              onClick={() => (player.playing ? player.stop() : player.play())}
            >
              <span aria-hidden="true">{player.playing ? "■" : "▶"}</span> {player.playing ? "Stop" : "Play notation"}
            </button>
          ) : original ? (
            <audio
              ref={audioRef}
              className={css.audio}
              controls
              preload="metadata"
              src={api.assetUrl(original)}
              aria-label={`Original recording: ${original.filename}`}
              onTimeUpdate={(e) => setOriginalNote(e.currentTarget.paused ? null : noteAt(notes, e.currentTarget.currentTime, "source_onset_s"))}
              onPause={() => setOriginalNote(null)}
              onEnded={() => setOriginalNote(null)}
            />
          ) : null}
        </div>
        <p className="o-muted">
          {source === "notation"
            ? "Notation plays what is written, on a plain synth with in-between pitches kept. Compare it with the original to hear what the writing leaves out."
            : "The original is your recording, untouched."}
          {!original ? " There is no original recording for typed notes." : ""}
          {player.unavailable ? " This browser cannot play synthesised sound." : ""}
        </p>
        <p className="o-mono" aria-hidden={playingIndex < 0}>
          {playingIndex >= 0 ? `Playing note ${playingIndex + 1} of ${notes.length}: ${notes[playingIndex].name}` : "\u00a0"}
        </p>
      </div>

      {(conversion.missing ?? []).map((m) => (
        <div key={m.kind} className="o-note" data-noprint>
          <p>
            <strong>{rendererLabel(m.kind)} is not available.</strong> {m.message}
          </p>
          {m.alternative ? (
            <p className="o-row" style={{ marginTop: 6 }}>
              <span>Closest alternative: {rendererLabel(m.alternative)}.</span>
              {shown.some((o) => o.kind === m.alternative) ? (
                <button type="button" className="o-btn o-btn-small" onClick={() => onTab(m.alternative as string)}>
                  Show {rendererLabel(m.alternative)}
                </button>
              ) : null}
            </p>
          ) : null}
        </div>
      ))}

      {shown.length > 0 && active ? (
        <>
          <div role="tablist" aria-label="Notation views" className={css.tabs} data-noprint>
            {shown.map((o, i) => (
              <button
                key={o.kind}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`${baseId}-tab-${o.kind}`}
                aria-selected={o.kind === active.kind}
                aria-controls={`${baseId}-panel`}
                tabIndex={o.kind === active.kind ? 0 : -1}
                className={css.tab}
                onClick={() => onTab(o.kind)}
                onKeyDown={(e) => onTabKey(e, i)}
              >
                {o.label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`${baseId}-panel`} aria-labelledby={`${baseId}-tab-${active.kind}`} className={css.tabPanel}>
            {(active.pages?.length ?? 0) > 0 ? (
              <SvgNotation
                pages={active.pages ?? []}
                noteIds={noteIds}
                lowIds={lowIds}
                currentId={currentId}
                selectedId={selectedNoteId}
                onSelect={onSelectNote}
                label={`${active.label} for ${registry.formatName(conversion.output_format)}. A description in words follows.`}
                describedBy={describe ? describeId : undefined}
              />
            ) : (
              <TextNotation
                text={active.text ?? ""}
                tokens={active.tokens ?? []}
                lowIds={lowIds}
                currentId={currentId}
                selectedId={selectedNoteId}
                onSelect={onSelectNote}
                label={`${active.label} for ${registry.formatName(conversion.output_format)}`}
              />
            )}
            {(active.warnings ?? []).map((w) => (
              <p key={w} className="o-note">
                {w}
              </p>
            ))}
          </div>
        </>
      ) : (
        <p className="o-note">No notation could be drawn for this conversion. The description below still applies.</p>
      )}

      <div className={`o-stack ${css.legend}`} data-noprint>
        <h3>Reading the marks</h3>
        <ul>
          <li>
            <span className={`${css.note} ${css.noteLow} ${css.legendSwatch}`}>
              note
              <span className={css.lowMark} aria-hidden="true">
                ?
              </span>
            </span>{" "}
            A dashed outline with a question mark: Orin was unsure of this note (confidence under 50%).
            {lowIds.size > 0 ? ` ${lowIds.size} of ${notes.length} notes are marked.` : " No notes are marked here."}
          </li>
          <li>
            <span className={`${css.note} ${css.noteCurrent} ${css.legendSwatch}`}>note</span> A filled box: the note sounding now.
          </li>
          <li>
            <span className={`${css.note} ${css.noteSelected} ${css.legendSwatch}`}>note</span> A solid outline: the note you selected.
          </li>
        </ul>
        <div className="o-row">
          <button type="button" className="o-btn o-btn-small" onClick={() => step(-1)} disabled={notes.length === 0}>
            Previous note
          </button>
          <button type="button" className="o-btn o-btn-small" onClick={() => step(1)} disabled={notes.length === 0}>
            Next note
          </button>
          {selected ? (
            <button type="button" className="o-btn o-btn-small o-btn-quiet" onClick={() => onSelectNote(null)}>
              Clear selection
            </button>
          ) : null}
        </div>
        <p role="status" aria-live="polite">
          {selected
            ? `Selected note ${selectedIndex + 1} of ${notes.length}: ${selected.name}, ${Math.round(selected.cents)} cents from the tonic` +
              (Math.abs(selected.cents_delta) >= 1
                ? `, written ${Math.abs(Math.round(selected.cents_delta))} cents ${selected.cents_delta > 0 ? "above" : "below"} what was performed`
                : "") +
              `. Confidence ${confidenceText(selected.confidence)}` +
              (lowIds.has(selected.id) ? ", marked as unsure." : ".")
            : "No note selected."}
        </p>
      </div>

      <div className="o-stack">
        <h3>In plain words</h3>
        {describe?.text ? (
          <p id={describeId} className={css.describe}>
            {describe.text}
          </p>
        ) : (
          <p id={describeId} className="o-muted">
            {notes.length > 0
              ? `A melody of ${notes.length} notes: ${notes.slice(0, 24).map((n) => n.name).join(" ")}${notes.length > 24 ? " ..." : ""}`
              : "No description is available for this conversion."}
          </p>
        )}
        <p className="o-muted" data-noprint>
          Pitches are measured in <Term name="cents">cents</Term> from the <Term name={target?.tonic_word ?? "tonic"}>{target?.tonic_word ?? "tonic"}</Term>.
          Transcription is approximate by design.
        </p>
      </div>
    </section>
  );
}
