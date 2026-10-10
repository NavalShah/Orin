// A small note synth shared by the Notation Studio ("play notation") and the Workspace
// (MIDI clips). Pitch is a frequency plus a detune in cents, so microtones play as
// written (spec 9.2, 12.6). Works on an AudioContext or an OfflineAudioContext.

export type SynthNote = {
  freqHz: number;
  start: number; // seconds from the start of the sequence
  duration: number;
  velocity?: number; // 0..1
  detuneCents?: number;
};

export const centsToHz = (tonicHz: number, cents: number) => tonicHz * 2 ** (cents / 1200);
export const midiToHz = (midi: number, a4 = 440) => a4 * 2 ** ((midi - 69) / 12);
export const hzToMidi = (hz: number, a4 = 440) => 69 + 12 * Math.log2(hz / a4);

const ATTACK = 0.012;
const RELEASE = 0.06;

/** Schedule one note at an absolute context time. Returns its oscillators so it can be stopped. */
export function playNote(
  ctx: BaseAudioContext,
  dest: AudioNode,
  n: { freqHz: number; when: number; duration: number; velocity?: number; detuneCents?: number },
): OscillatorNode[] {
  const peak = 0.22 * Math.max(0.05, Math.min(1, n.velocity ?? 0.7));
  const end = n.when + Math.max(n.duration, 0.03);
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0.0001, n.when);
  gain.gain.exponentialRampToValueAtTime(peak, n.when + ATTACK);
  gain.gain.exponentialRampToValueAtTime(peak * 0.6, Math.max(n.when + ATTACK + 0.001, end - RELEASE));
  gain.gain.exponentialRampToValueAtTime(0.0001, end);
  gain.connect(dest);

  const voices: Array<[OscillatorType, number, number]> = [
    ["triangle", 1, 1],
    ["sine", 2, 0.25],
  ];
  return voices.map(([type, ratio, level]) => {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = type;
    osc.frequency.value = n.freqHz * ratio;
    osc.detune.value = n.detuneCents ?? 0;
    g.gain.value = level;
    osc.connect(g);
    g.connect(gain);
    osc.start(n.when);
    osc.stop(end + 0.02);
    return osc;
  });
}

/** Schedule a whole sequence starting at ``startAt`` (context time). */
export function scheduleNotes(ctx: BaseAudioContext, dest: AudioNode, notes: SynthNote[], startAt: number) {
  const oscillators: OscillatorNode[] = [];
  let endTime = startAt;
  for (const n of notes) {
    oscillators.push(...playNote(ctx, dest, { ...n, when: startAt + n.start }));
    endTime = Math.max(endTime, startAt + n.start + n.duration);
  }
  return {
    endTime,
    stop() {
      for (const osc of oscillators) {
        try {
          osc.stop();
        } catch {
          // already stopped
        }
      }
    },
  };
}

/** A converted score as synth notes: what the notation says, not what was performed. */
export function scoreToSynthNotes(score: {
  options: { tonic_hz: number };
  notes: ReadonlyArray<{ cents: number; onset_s: number; duration_s: number; velocity?: number }>;
}): SynthNote[] {
  return score.notes.map((n) => ({
    freqHz: centsToHz(score.options.tonic_hz, n.cents),
    start: n.onset_s,
    duration: n.duration_s,
    velocity: (n.velocity ?? 80) / 127,
  }));
}
