// A format-0 Standard MIDI File writer that keeps microtones (spec 12.8, 12.5).
// MIDI has no per-note tuning, so each note carries its detune as a pitch bend sent just
// before its note-on. Notes alternate between two channels, so the bend for the next note
// does not retune the tail of the one before. The bend range is set to +/-2 semitones
// with RPN 0. This suits a melody line; chords of differently detuned notes on one
// channel would share a bend.

export type SmfNote = {
  midi: number;
  detuneCents: number;
  startSec: number;
  durSec: number;
  velocity: number; // 0..1
};

export const PPQ = 480;
export const BEND_RANGE_SEMITONES = 2;
const BEND_CENTER = 8192;

/** A 14-bit pitch-bend value for a detune in cents. 8192 is no bend. */
export function bendValue(cents: number): number {
  const value = BEND_CENTER + Math.round((cents / (BEND_RANGE_SEMITONES * 100)) * BEND_CENTER);
  return Math.max(0, Math.min(16383, value));
}

/** MIDI variable-length quantity. */
export function vlq(value: number): number[] {
  let v = Math.max(0, Math.floor(value));
  const bytes = [v & 0x7f];
  while ((v >>= 7) > 0) bytes.unshift((v & 0x7f) | 0x80);
  return bytes;
}

const u32 = (v: number) => [(v >>> 24) & 0xff, (v >>> 16) & 0xff, (v >>> 8) & 0xff, v & 0xff];
const u16 = (v: number) => [(v >>> 8) & 0xff, v & 0xff];
const ascii = (s: string) => Array.from(s, (c) => c.charCodeAt(0));

type Event = { tick: number; order: number; bytes: number[] };

export function encodeMidi(notes: readonly SmfNote[], tempo: number): Uint8Array<ArrayBuffer> {
  const bpm = Math.max(1, tempo);
  const toTick = (sec: number) => Math.max(0, Math.round((sec * bpm * PPQ) / 60));
  const events: Event[] = [];

  const usPerQuarter = Math.round(60_000_000 / bpm);
  events.push({ tick: 0, order: -3, bytes: [0xff, 0x51, 0x03, ...u32(usPerQuarter).slice(1)] });
  for (const ch of [0, 1]) {
    const cc = 0xb0 | ch;
    events.push({
      tick: 0,
      order: -2,
      bytes: [
        [cc, 101, 0], // RPN MSB: 0
        [cc, 100, 0], // RPN LSB: 0 = pitch-bend sensitivity
        [cc, 6, BEND_RANGE_SEMITONES], // data entry: semitones
        [cc, 38, 0], // data entry: cents
        [cc, 101, 127], // RPN null, so later data entry changes nothing
        [cc, 100, 127],
      ].flatMap((message, i) => (i === 0 ? message : [0, ...message])),
    });
  }

  const sorted = notes
    .filter((n) => Number.isFinite(n.midi) && n.durSec > 0)
    .slice()
    .sort((a, b) => a.startSec - b.startSec || a.midi - b.midi);
  sorted.forEach((n, i) => {
    const ch = i % 2;
    const key = Math.max(0, Math.min(127, Math.round(n.midi)));
    const velocity = Math.max(1, Math.min(127, Math.round(n.velocity * 127)));
    const on = toTick(n.startSec);
    const off = Math.max(on + 1, toTick(n.startSec + n.durSec));
    const bend = bendValue(n.detuneCents);
    events.push({ tick: on, order: 1, bytes: [0xe0 | ch, bend & 0x7f, (bend >> 7) & 0x7f] });
    events.push({ tick: on, order: 2, bytes: [0x90 | ch, key, velocity] });
    events.push({ tick: off, order: 0, bytes: [0x80 | ch, key, 0] });
  });

  // Stable: at one tick, note-offs come first, then the bend, then the note-on it belongs to.
  const indexed = events.map((e, i) => ({ e, i }));
  indexed.sort((a, b) => a.e.tick - b.e.tick || a.e.order - b.e.order || a.i - b.i);

  const track: number[] = [];
  let last = 0;
  for (const { e } of indexed) {
    track.push(...vlq(e.tick - last), ...e.bytes);
    last = e.tick;
  }
  track.push(0, 0xff, 0x2f, 0x00); // end of track

  return new Uint8Array([
    ...ascii("MThd"),
    ...u32(6),
    ...u16(0), // format 0
    ...u16(1), // one track
    ...u16(PPQ),
    ...ascii("MTrk"),
    ...u32(track.length),
    ...track,
  ]);
}
