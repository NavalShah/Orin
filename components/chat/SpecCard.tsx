"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, errorText, type Card, type FormatsResponse, type Job, type MusicSpec } from "@/lib/api";
import { clock, pct } from "@/lib/format";
import { useFocusAfter } from "@/components/shell/useFocusAfter";
import css from "./Chat.module.css";

const VOCALS: Record<MusicSpec["vocals"], string> = {
  none: "No vocals",
  wordless: "Wordless vocals",
  lyrics: "Vocals with lyrics",
};

const list = (text: string) => text.split(",").map((s) => s.trim()).filter(Boolean);

/** Apply the edit form to a spec, keeping the sections in step with the new genre and length. */
function applyEdit(spec: MusicSpec, edit: Partial<MusicSpec>): MusicSpec {
  const next = { ...spec, ...edit };
  const sections = spec.sections ?? [];
  if (sections.length === 1) {
    next.sections = [{ ...sections[0], genre_id: sections[0].genre_id === spec.genre_id ? next.genre_id : sections[0].genre_id, duration_s: next.duration_s }];
  } else if (sections.length > 1 && next.duration_s !== spec.duration_s) {
    const total = sections.reduce((sum, s) => sum + s.duration_s, 0) || 1;
    next.sections = sections.map((s) => ({ ...s, duration_s: Math.max(1, Math.round((s.duration_s / total) * next.duration_s)) }));
  }
  return next;
}

/**
 * "Here's what I'll make" (spec 10.3 step 6): the refined spec as chips, with the assumptions
 * Orin made, an Edit mode, and Generate. Nothing is generated until the user confirms.
 */
export function SpecCard({
  card,
  threadId,
  registry,
  onDone,
}: {
  card: Card;
  threadId?: string;
  registry: FormatsResponse;
  /** Called when the track is ready, so the thread can be reloaded to show its audio card. */
  onDone?: () => void;
}) {
  const titleId = useId();
  const [spec, setSpec] = useState<MusicSpec | null>(card.spec);
  const [editing, setEditing] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const focusAfter = useFocusAfter();
  const editId = `${titleId}-edit`;

  useEffect(() => () => abortRef.current?.abort(), []);

  function closeEdit() {
    focusAfter(editId);
    setEditing(false);
  }

  if (!spec) return null;
  const name = (id: string) => registry.formats.find((f) => f.id === id)?.display_name ?? id;
  const sections = spec.sections ?? [];
  // The server's notes repeat the spec's assumptions; show each sentence once.
  const notes = [...new Set([...(spec.assumptions ?? []), ...(card.notes ?? [])])];
  const maxSeconds = registry.limits.max_generate_seconds;

  async function generate() {
    if (!spec) return;
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(true);
    setDone(false);
    setError(null);
    setJob(null);
    try {
      const ref = await api.generate(spec, threadId);
      const final = await api.watchJob(ref.job_id, setJob, ac.signal);
      if (final.status === "failed") {
        setError(final.error ? (final.error.hint ? `${final.error.message} ${final.error.hint}` : final.error.message) : "The track could not be made.");
      } else if (final.status === "cancelled") {
        setError("Generation was cancelled.");
      } else {
        setDone(true);
        onDone?.();
      }
    } catch (err) {
      if ((err as Error)?.name !== "AbortError") setError(errorText(err));
    } finally {
      setBusy(false);
      setJob(null);
    }
  }

  function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!spec) return;
    const data = new FormData(e.currentTarget);
    const tempo = Number(data.get("tempo"));
    const duration = Math.round(Number(data.get("duration")));
    setSpec(
      applyEdit(spec, {
        genre_id: String(data.get("genre") || spec.genre_id),
        tempo_bpm: tempo > 0 ? Math.round(tempo) : null,
        duration_s: duration > 0 ? Math.min(duration, maxSeconds) : spec.duration_s,
        mood: list(String(data.get("mood") ?? "")),
        instruments: list(String(data.get("instruments") ?? "")),
        vocals: String(data.get("vocals") || spec.vocals) as MusicSpec["vocals"],
        structure_notes: String(data.get("structure") ?? "").trim() || null,
      }),
    );
    closeEdit();
  }

  return (
    <section className={css.card} aria-labelledby={titleId}>
      <h3 id={titleId}>Here&apos;s what I&apos;ll make</h3>
      {card.text ? <p>{card.text}</p> : null}

      {editing ? (
        <form className="o-stack" onSubmit={save}>
          <div className={css.formGrid}>
            <label className="o-label">
              Genre
              <select className="o-select" name="genre" defaultValue={spec.genre_id} autoFocus>
                {registry.formats.some((f) => f.id === spec.genre_id) ? null : <option value={spec.genre_id}>{spec.genre_id} (as written)</option>}
                {registry.families.map((family) => (
                  <optgroup key={family.id} label={family.label}>
                    {registry.formats
                      .filter((f) => family.format_ids.includes(f.id))
                      .map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.display_name}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <label className="o-label">
              Tempo (beats a minute, blank for none)
              <input className="o-input" name="tempo" type="number" min={30} max={300} defaultValue={spec.tempo_bpm ?? ""} />
            </label>
            <label className="o-label">
              Length in seconds (up to {maxSeconds})
              <input className="o-input" name="duration" type="number" min={10} max={maxSeconds} defaultValue={spec.duration_s} required />
            </label>
            <label className="o-label">
              Vocals
              <select className="o-select" name="vocals" defaultValue={spec.vocals}>
                {Object.entries(VOCALS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="o-label">
              Mood (separate with commas)
              <input className="o-input" name="mood" defaultValue={(spec.mood ?? []).join(", ")} />
            </label>
            <label className="o-label">
              Instruments (separate with commas)
              <input className="o-input" name="instruments" defaultValue={(spec.instruments ?? []).join(", ")} />
            </label>
          </div>
          <label className="o-label">
            Notes on the structure
            <input className="o-input" name="structure" defaultValue={spec.structure_notes ?? ""} />
          </label>
          <div className="o-row">
            <button type="submit" className="o-btn o-btn-primary">
              Save changes
            </button>
            <button type="button" className="o-btn" onClick={closeEdit}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <ul className={css.chips} aria-label="The plan">
            <li>
              <span className={css.chipKey}>Genre</span> {name(spec.genre_id)}
            </li>
            <li>
              <span className={css.chipKey}>Tempo</span> {spec.tempo_bpm ? `${spec.tempo_bpm} BPM` : "not set"}
            </li>
            <li>
              <span className={css.chipKey}>Length</span> {clock(spec.duration_s)}
            </li>
            <li>
              <span className={css.chipKey}>Mood</span> {(spec.mood ?? []).join(", ") || "not set"}
            </li>
            <li>
              <span className={css.chipKey}>Instruments</span> {(spec.instruments ?? []).join(", ") || "not set"}
            </li>
            <li>
              <span className={css.chipKey}>Vocals</span> {VOCALS[spec.vocals] ?? spec.vocals}
            </li>
            {spec.key_or_tonic ? (
              <li>
                <span className={css.chipKey}>Key or tonic</span> {spec.key_or_tonic}
              </li>
            ) : null}
            {spec.mode_hint ? (
              <li>
                <span className={css.chipKey}>Mode hint</span> {spec.mode_hint} (a hint, not a guarantee)
              </li>
            ) : null}
          </ul>

          {sections.length > 0 ? (
            <div>
              <h4 className={css.cardLabel}>Sections</h4>
              <ol className={css.sections}>
                {sections.map((s, i) => (
                  <li key={i}>
                    <strong>{name(s.genre_id)}</strong>: {s.description} <span className="o-mono">({clock(s.duration_s)})</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          {spec.structure_notes ? <p>Structure: {spec.structure_notes}</p> : null}
          {(spec.avoid ?? []).length > 0 ? <p>Avoiding: {(spec.avoid ?? []).join(", ")}</p> : null}
          {spec.learning_goal ? <p>Listen for: {spec.learning_goal}</p> : null}

          {notes.length > 0 ? (
            <div>
              <h4 className={css.cardLabel}>Assumptions and notes</h4>
              <ul className={css.notes}>
                {notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="o-row">
            <button type="button" className="o-btn o-btn-primary" onClick={() => void generate()} disabled={busy}>
              {done ? "Generate again" : "Generate"}
            </button>
            <button type="button" id={editId} className="o-btn" onClick={() => setEditing(true)} disabled={busy}>
              Edit
            </button>
            {busy ? (
              <button type="button" className="o-btn" onClick={() => abortRef.current?.abort()}>
                Stop waiting
              </button>
            ) : null}
          </div>
        </>
      )}

      {busy ? (
        <div className="o-row" style={{ flexWrap: "nowrap" }}>
          <progress className="o-progress" max={1} {...(job ? { value: Math.min(1, Math.max(0, job.progress)) } : {})} aria-label="Composing progress" />
          <span className="o-mono" aria-hidden="true">
            {job ? pct(job.progress) : ""}
          </span>
        </div>
      ) : null}
      <p role="status" aria-live="polite" className={busy || done ? undefined : "o-sr-only"}>
        {busy ? `Composing...${job?.message ? ` ${job.message}` : ""}` : done ? "The track is ready. It is in this chat and in your Library." : ""}
      </p>
      {error ? (
        <div className="o-alert" role="alert">
          <p>{error}</p>
          <p style={{ marginTop: 8 }}>
            <button type="button" className="o-btn o-btn-small" onClick={() => void generate()}>
              Try again
            </button>
          </p>
        </div>
      ) : null}
    </section>
  );
}
