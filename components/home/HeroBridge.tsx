"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { midiToHz, playNote } from "@/lib/audio/synth";
import { GAP_S, PHRASE, lostItems, midiOf, pitchLine, placed, totalDuration } from "./melody";
import { audio, playLine } from "./sound";
import css from "./home.module.css";

// Drawing area, in SVG units.
const W = 600;
const H = 210;
const PAD = { left: 34, right: 12, top: 16, bottom: 34 };
const LOW = -110;
const HIGH = 1010;
const MORPH_MS = 520;

const WHITE_KEYS = [60, 62, 64, 65, 67, 69, 71, 72];
const KEY_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
const keyName = (midi: number) => KEY_NAMES[midi % 12];

const x = (t: number, total: number) => PAD.left + (t / total) * (W - PAD.left - PAD.right);
const y = (cents: number) => H - PAD.bottom - ((cents - LOW) / (HIGH - LOW)) * (H - PAD.top - PAD.bottom);

function reducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * The home page's one moving part: a short melody as a pitch line, with a switch between
 * how it was performed and what piano keys can hold. Flipping it is the whole idea of
 * Orin in a few seconds: the notes survive, the movement between them does not.
 */
export function HeroBridge() {
  const notes = useMemo(() => placed(), []);
  const total = useMemo(() => totalDuration(), []);
  const lost = useMemo(() => lostItems(), []);
  const [written, setWritten] = useState(false);
  const [mix, setMix] = useState(0);
  const [active, setActive] = useState<string | null>(null);
  const [soundless, setSoundless] = useState(false);
  const frame = useRef<number | null>(null);
  const timers = useRef<number[]>([]);
  const voices = useRef<OscillatorNode[]>([]);

  // Ease the line from one version to the other; jump when motion is reduced.
  useEffect(() => {
    const target = written ? 1 : 0;
    if (reducedMotion()) {
      setMix(target);
      return;
    }
    const from = mix;
    const started = performance.now();
    const step = (now: number) => {
      const k = Math.min(1, (now - started) / MORPH_MS);
      const eased = k * k * (3 - 2 * k);
      setMix(from + (target - from) * eased);
      if (k < 1) frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
    // ``mix`` is read once as the starting point; re-running on every frame would restart the ease.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [written]);

  const stop = useCallback(() => {
    timers.current.forEach((id) => window.clearTimeout(id));
    timers.current = [];
    voices.current.forEach((osc) => {
      try {
        osc.stop();
      } catch {
        // already ended
      }
    });
    voices.current = [];
    setActive(null);
  }, []);

  useEffect(() => stop, [stop]);

  const sound = useCallback(
    (ids: string[]) => {
      stop();
      const ctx = audio();
      if (!ctx) {
        setSoundless(true);
        return;
      }
      const chosen = notes.filter((n) => ids.includes(n.id));
      const origin = chosen[0]?.start ?? 0;
      const t0 = ctx.currentTime + 0.05;
      for (const note of chosen) {
        const at = note.start - origin;
        voices.current.push(playLine(ctx, pitchLine(note, written ? 1 : 0, 64), t0 + at, note.dur));
        timers.current.push(window.setTimeout(() => setActive(note.id), at * 1000));
      }
      const last = chosen[chosen.length - 1];
      if (last) timers.current.push(window.setTimeout(() => setActive(null), (last.start - origin + last.dur + GAP_S) * 1000));
    },
    [notes, written, stop],
  );

  const pressKey = (midi: number) => {
    const ctx = audio();
    if (!ctx) {
      setSoundless(true);
      return;
    }
    playNote(ctx, ctx.destination, { freqHz: midiToHz(midi), when: ctx.currentTime, duration: 0.5, velocity: 0.7 });
  };

  const activeMidi = active ? midiOf(notes.find((n) => n.id === active) ?? PHRASE[0]) : null;
  const keyLines = [...new Set(PHRASE.map((n) => n.written))];

  return (
    <figure className={css.bridge} aria-labelledby="orin-bridge-caption">
      <div className={css.bridgeTop}>
        <div className={css.segment} role="group" aria-label="Which version to show">
          <button type="button" aria-pressed={!written} onClick={() => setWritten(false)}>
            As performed
          </button>
          <button type="button" aria-pressed={written} onClick={() => setWritten(true)}>
            On piano keys
          </button>
        </div>
        <button type="button" className={css.play} onClick={() => sound(notes.map((n) => n.id))}>
          <span aria-hidden="true">▶</span> Play this version
        </button>
      </div>

      <svg
        className={css.plot}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={
          written
            ? "The melody as piano keys hold it: six flat steps, one per note."
            : "The melody as performed: a pitch line that wavers on the third note and slides up into the fourth."
        }
      >
        {/* The pitches a piano has for this phrase. They brighten when the line lands on them. */}
        {keyLines.map((cents) => (
          <g key={cents} className={css.keyLine} style={{ opacity: 0.35 + 0.65 * mix }}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(cents)} y2={y(cents)} />
            <text x={PAD.left - 8} y={y(cents) + 4} textAnchor="end">
              {keyName(60 + cents / 100)}
            </text>
          </g>
        ))}
        {notes.map((note) => {
          const points = pitchLine(note, mix)
            .map((p) => `${x(note.start + p.t, total).toFixed(1)},${y(p.cents).toFixed(1)}`)
            .join(" ");
          return (
            <polyline
              key={note.id}
              points={points}
              className={`${css.line} ${active === note.id ? css.lineActive : ""} ${note.lost ? css.lineMoves : ""}`}
            />
          );
        })}
        {notes.map((note) => (
          <text key={note.id} className={css.noteName} x={x(note.start + note.dur / 2, total)} y={H - 10} textAnchor="middle">
            {written ? note.letter : note.swara}
          </text>
        ))}
      </svg>

      <div className={css.noteButtons} role="group" aria-label="Hear one note of the melody">
        {notes.map((note) => (
          <button
            key={note.id}
            type="button"
            className={active === note.id ? css.noteOn : undefined}
            style={{ flexGrow: note.dur }}
            onClick={() => sound([note.id])}
            aria-label={`Hear the note ${written ? note.letter : note.swara}${note.lost && !written ? ", with its movement" : ""}`}
          >
            {written ? note.letter : note.swara}
          </button>
        ))}
      </div>

      <div className={css.keyboard} role="group" aria-label="Piano keys. Press one to hear it.">
        {WHITE_KEYS.map((midi, i) => (
          <span className={css.whiteSlot} key={midi}>
            <button
              type="button"
              className={`${css.key} ${activeMidi === midi ? css.keyOn : ""}`}
              aria-label={`Play ${keyName(midi)}`}
              onClick={() => pressKey(midi)}
            >
              {keyName(midi)}
            </button>
            {i < WHITE_KEYS.length - 1 && WHITE_KEYS[i + 1] - midi === 2 ? (
              <button type="button" className={css.blackKey} aria-label={`Play ${keyName(midi + 1)}`} onClick={() => pressKey(midi + 1)} />
            ) : null}
          </span>
        ))}
      </div>

      <figcaption id="orin-bridge-caption" className={css.caption} aria-live="polite">
        {written ? (
          <>
            <strong>
              {lost.length} {lost.length === 1 ? "thing" : "things"} did not make it onto the keys.
            </strong>
            <ul>
              {lost.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <strong>This is the performance.</strong> The voice moves between the keys. Switch to "On piano keys" to see what a
            keyboard can keep.
          </>
        )}
        {soundless ? <span className={css.quiet}> Sound is not available in this browser; the drawing still works.</span> : null}
      </figcaption>
      <p className={css.fine}>An illustration made for this page, not a real recording or a particular raga.</p>
    </figure>
  );
}
