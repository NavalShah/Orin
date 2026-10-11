// The short phrase the home page draws and plays. It is an illustration made for this
// page, not a transcription of a real performance and not a claim about any raga.
//
// "Performed" is a pitch line in cents above the tonic, with a wavering note and a slide.
// "Written" is what piano keys can hold: one fixed pitch per note. Everything the switch
// on the home page shows is computed from these two.

export const TONIC_HZ = 261.63; // C4, so the written notes are plain white keys
export const TONIC_MIDI = 60;
export const GAP_S = 0.07;

export type PhraseNote = {
  id: string;
  swara: string; // the name in the source tradition
  letter: string; // the piano key it is written as
  cents: number; // where the performance settles
  written: number; // the nearest piano key, in cents
  dur: number;
  /** A slow, wide wavering around the note. */
  waver?: { halfExtent: number; rateHz: number };
  /** A slide into the note from another pitch. */
  slide?: { from: number; dur: number };
  /** What a piano score loses on this note, in plain words. */
  lost?: string;
};

export const PHRASE: PhraseNote[] = [
  { id: "h1", swara: "Sa", letter: "C", cents: 0, written: 0, dur: 0.5 },
  { id: "h2", swara: "Ri", letter: "D", cents: 204, written: 200, dur: 0.5 },
  {
    id: "h3", swara: "Ga", letter: "E", cents: 386, written: 400, dur: 1.2,
    waver: { halfExtent: 70, rateHz: 5 },
    lost: "The slow wavering on Ga: a key holds one pitch.",
  },
  {
    id: "h4", swara: "Pa", letter: "G", cents: 702, written: 700, dur: 0.9,
    slide: { from: 386, dur: 0.32 },
    lost: "The slide up into Pa: keys step, they do not glide.",
  },
  { id: "h5", swara: "Dha", letter: "A", cents: 884, written: 900, dur: 0.5 },
  { id: "h6", swara: "Pa", letter: "G", cents: 702, written: 700, dur: 0.8 },
];

export type Placed = PhraseNote & { start: number };

export function placed(phrase: PhraseNote[] = PHRASE): Placed[] {
  let t = 0;
  return phrase.map((note) => {
    const at = { ...note, start: t };
    t += note.dur + GAP_S;
    return at;
  });
}

export function totalDuration(phrase: PhraseNote[] = PHRASE): number {
  return phrase.reduce((sum, n) => sum + n.dur + GAP_S, 0) - GAP_S;
}

/** The performed pitch, in cents, ``t`` seconds into the note. */
export function performedCents(note: PhraseNote, t: number): number {
  if (note.slide && t < note.slide.dur) {
    const k = t / note.slide.dur;
    const eased = k * k * (3 - 2 * k);
    return note.slide.from + (note.cents - note.slide.from) * eased;
  }
  if (note.waver) {
    // Fade the wavering in and out so the note starts and ends on its own pitch.
    const edge = Math.min(1, t / 0.12, (note.dur - t) / 0.12);
    return note.cents + note.waver.halfExtent * Math.max(0, edge) * Math.sin(2 * Math.PI * note.waver.rateHz * t);
  }
  return note.cents;
}

/**
 * The note's pitch line as ``samples`` points. ``mix`` 0 is the performance, 1 is the
 * piano's version, and values between are the morph the switch animates through.
 */
export function pitchLine(note: PhraseNote, mix: number, samples = 48): Array<{ t: number; cents: number }> {
  const m = Math.min(1, Math.max(0, mix));
  return Array.from({ length: samples }, (_, i) => {
    const t = (note.dur * i) / (samples - 1);
    const performed = performedCents(note, t);
    return { t, cents: performed + (note.written - performed) * m };
  });
}

export function lostItems(phrase: PhraseNote[] = PHRASE): string[] {
  return phrase.flatMap((n) => (n.lost ? [n.lost] : []));
}

export const midiOf = (note: PhraseNote) => TONIC_MIDI + Math.round(note.written / 100);
export const centsToHz = (cents: number) => TONIC_HZ * 2 ** (cents / 1200);
