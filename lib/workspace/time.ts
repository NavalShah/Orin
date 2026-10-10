// Time helpers for the Workspace. Clip and note positions are stored in seconds; the
// bar:beat grid is derived from the tempo for display and snapping (spec 12.7).

export type TimeSignature = [number, number];

const round6 = (x: number) => Math.round(x * 1e6) / 1e6;

export const secPerBeat = (tempo: number) => 60 / Math.max(1, tempo);
/** Quarter-note beats in one bar: 4 in 4/4, 3 in 3/4, 3 in 6/8. */
export const beatsPerBar = (ts: TimeSignature) => (ts[0] * 4) / ts[1];
export const secPerBar = (tempo: number, ts: TimeSignature) => secPerBeat(tempo) * beatsPerBar(ts);

/** Length of one grid step. ``division`` is steps per beat: 1 = a beat, 4 = a sixteenth note. */
export const gridStepSec = (tempo: number, division: number) => secPerBeat(tempo) / division;

/** The nearest grid line to ``t``. Never negative. */
export function snapSec(t: number, tempo: number, division = 4): number {
  const step = gridStepSec(tempo, division);
  return Math.max(0, round6(Math.round(t / step) * step));
}

/** 1-based bar and beat at a time, plus the fraction of the beat that has passed. */
export function barBeatAt(sec: number, tempo: number, ts: TimeSignature) {
  const beats = Math.max(0, sec) / secPerBeat(tempo) + 1e-9;
  const perBar = beatsPerBar(ts);
  const bar = Math.floor(beats / perBar);
  const inBar = beats - bar * perBar;
  return { bar: bar + 1, beat: Math.floor(inBar) + 1, fraction: inBar - Math.floor(inBar) };
}

/** "3:2" = bar 3, beat 2. */
export function formatBarsBeats(sec: number, tempo: number, ts: TimeSignature): string {
  const { bar, beat } = barBeatAt(sec, tempo, ts);
  return `${bar}:${beat}`;
}

/** "01:23.4" */
export function formatClock(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  const whole = Math.floor(rest);
  const tenth = Math.floor((rest - whole) * 10);
  return `${String(m).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${tenth}`;
}

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
export const isBlackKey = (midi: number) => [1, 3, 6, 8, 10].includes(((midi % 12) + 12) % 12);
/** 60 -> "C4". */
export const midiName = (midi: number) => `${NOTE_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;

export const signedCents = (cents: number) => `${cents > 0 ? "+" : ""}${Math.round(cents * 10) / 10}`;

/** Spoken position: "bar 3" or "bar 3 beat 2". */
export function spokenPosition(sec: number, tempo: number, ts: TimeSignature): string {
  const { bar, beat } = barBeatAt(sec, tempo, ts);
  return beat > 1 ? `bar ${bar} beat ${beat}` : `bar ${bar}`;
}

/** "Audio clip Lead vocal, starts at bar 3, 4.2 seconds" */
export function clipLabel(
  clip: { kind: "audio" | "midi"; name: string; startSec: number; lengthSec: number },
  tempo: number,
  ts: TimeSignature,
): string {
  const kind = clip.kind === "audio" ? "Audio" : "MIDI";
  return `${kind} clip ${clip.name}, starts at ${spokenPosition(clip.startSec, tempo, ts)}, ${clip.lengthSec.toFixed(1)} seconds`;
}

/** "Note D3 +50 cents, starts at 1.25 seconds, 0.50 seconds long" */
export function noteLabel(note: { midi: number; detuneCents: number; startSec: number; durSec: number }): string {
  const detune = Math.abs(note.detuneCents) >= 0.05 ? ` ${signedCents(note.detuneCents)} cents` : "";
  return `Note ${midiName(note.midi)}${detune}, starts at ${note.startSec.toFixed(2)} seconds, ${note.durSec.toFixed(2)} seconds long`;
}
