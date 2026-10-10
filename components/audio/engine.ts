// One shared AudioContext for the Orin screens, created on first use (browsers only
// allow sound after a user gesture, so call these from click handlers).

let shared: AudioContext | null = null;

export function audioContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  if (!shared || shared.state === "closed") shared = new Ctor();
  if (shared.state === "suspended") void shared.resume();
  return shared;
}

/** A short sine tone, for previewing a tonic candidate. Returns a function that stops it early. */
export function playTone(hz: number, seconds = 1.5): () => void {
  const ctx = audioContext();
  if (!ctx || !(hz > 0)) return () => undefined;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  const t = ctx.currentTime + 0.02;
  osc.type = "sine";
  osc.frequency.value = hz;
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.exponentialRampToValueAtTime(0.2, t + 0.04);
  gain.gain.setValueAtTime(0.2, t + seconds - 0.15);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + seconds);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(t);
  osc.stop(t + seconds + 0.05);
  return () => {
    try {
      osc.stop();
    } catch {
      // already stopped
    }
  };
}
