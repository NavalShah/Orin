// Plays one note of the home-page phrase along its pitch line, so the wavering and the
// slide are heard as drawn. Nothing here talks to the server.
import { centsToHz } from "./melody";

let shared: AudioContext | null = null;

/** The page's one AudioContext, created on the first user gesture. Null if unsupported. */
export function audio(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  shared ??= new Ctor();
  if (shared.state === "suspended") void shared.resume();
  return shared;
}

export function playLine(ctx: AudioContext, line: ReadonlyArray<{ cents: number }>, when: number, dur: number): OscillatorNode {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "triangle";
  const curve = new Float32Array(line.map((p) => centsToHz(p.cents)));
  osc.frequency.setValueAtTime(curve[0], when);
  if (curve.length > 1) osc.frequency.setValueCurveAtTime(curve, when, dur);
  gain.gain.setValueAtTime(0.0001, when);
  gain.gain.exponentialRampToValueAtTime(0.16, when + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.11, when + Math.max(0.05, dur - 0.08));
  gain.gain.exponentialRampToValueAtTime(0.0001, when + dur);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(when);
  osc.stop(when + dur + 0.03);
  return osc;
}
