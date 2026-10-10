// Synthesised drum voices and the metronome click (spec 12.6). No samples to load, and
// the same code runs on an AudioContext and an OfflineAudioContext. Each voice schedules
// itself at an absolute context time and returns its sources so they can be stopped.

export type DrumSound = (ctx: BaseAudioContext, dest: AudioNode, when: number, gain: number) => AudioScheduledSourceNode[];

const noiseCache = new WeakMap<BaseAudioContext, AudioBuffer>();

/** One second of white noise, made once per context. */
function noise(ctx: BaseAudioContext): AudioBuffer {
  let buffer = noiseCache.get(ctx);
  if (!buffer) {
    buffer = ctx.createBuffer(1, Math.max(1, Math.floor(ctx.sampleRate)), ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    noiseCache.set(ctx, buffer);
  }
  return buffer;
}

function noiseSource(ctx: BaseAudioContext, when: number, length: number): AudioBufferSourceNode {
  const src = ctx.createBufferSource();
  src.buffer = noise(ctx);
  // Start somewhere different each hit so repeated hits don't sound identical.
  src.start(when, Math.random() * 0.5, length);
  return src;
}

function filter(ctx: BaseAudioContext, type: BiquadFilterType, freq: number, q = 0.7): BiquadFilterNode {
  const f = ctx.createBiquadFilter();
  f.type = type;
  f.frequency.value = freq;
  f.Q.value = q;
  return f;
}

/** Kick: a sine with a fast pitch drop. */
export const kick: DrumSound = (ctx, dest, when, gain) => {
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(160, when);
  osc.frequency.exponentialRampToValueAtTime(45, when + 0.11);
  amp.gain.setValueAtTime(Math.max(0.0001, gain), when);
  amp.gain.exponentialRampToValueAtTime(0.0001, when + 0.32);
  osc.connect(amp);
  amp.connect(dest);
  osc.start(when);
  osc.stop(when + 0.34);
  return [osc];
};

/** Snare: filtered noise plus a short tone. */
export const snare: DrumSound = (ctx, dest, when, gain) => {
  const src = noiseSource(ctx, when, 0.2);
  const hp = filter(ctx, "highpass", 1500);
  const amp = ctx.createGain();
  amp.gain.setValueAtTime(Math.max(0.0001, gain * 0.7), when);
  amp.gain.exponentialRampToValueAtTime(0.0001, when + 0.18);
  src.connect(hp);
  hp.connect(amp);
  amp.connect(dest);

  const tone = ctx.createOscillator();
  const toneAmp = ctx.createGain();
  tone.type = "triangle";
  tone.frequency.setValueAtTime(210, when);
  tone.frequency.exponentialRampToValueAtTime(140, when + 0.08);
  toneAmp.gain.setValueAtTime(Math.max(0.0001, gain * 0.5), when);
  toneAmp.gain.exponentialRampToValueAtTime(0.0001, when + 0.1);
  tone.connect(toneAmp);
  toneAmp.connect(dest);
  tone.start(when);
  tone.stop(when + 0.12);
  return [src, tone];
};

/** Hat: high-passed noise, very short. */
export const hat: DrumSound = (ctx, dest, when, gain) => {
  const src = noiseSource(ctx, when, 0.08);
  const hp = filter(ctx, "highpass", 7000);
  const amp = ctx.createGain();
  amp.gain.setValueAtTime(Math.max(0.0001, gain * 0.45), when);
  amp.gain.exponentialRampToValueAtTime(0.0001, when + 0.05);
  src.connect(hp);
  hp.connect(amp);
  amp.connect(dest);
  return [src];
};

/** Clap: three quick noise bursts, then a short tail. */
export const clap: DrumSound = (ctx, dest, when, gain) => {
  const src = noiseSource(ctx, when, 0.26);
  const bp = filter(ctx, "bandpass", 1200, 0.9);
  const amp = ctx.createGain();
  const peak = Math.max(0.0001, gain * 0.8);
  for (let i = 0; i < 3; i++) {
    const t = when + i * 0.011;
    amp.gain.setValueAtTime(peak, t);
    amp.gain.exponentialRampToValueAtTime(peak * 0.15, t + 0.009);
  }
  amp.gain.setValueAtTime(peak, when + 0.033);
  amp.gain.exponentialRampToValueAtTime(0.0001, when + 0.24);
  src.connect(bp);
  bp.connect(amp);
  amp.connect(dest);
  return [src];
};

export const DRUM_SOUNDS = { kick, snare, hat, clap } satisfies Record<string, DrumSound>;

/** Metronome click. The first beat of the bar is higher and louder. */
export function click(ctx: BaseAudioContext, dest: AudioNode, when: number, accent: boolean): AudioScheduledSourceNode[] {
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  osc.type = "square";
  osc.frequency.value = accent ? 1760 : 1175;
  amp.gain.setValueAtTime(accent ? 0.22 : 0.13, when);
  amp.gain.exponentialRampToValueAtTime(0.0001, when + 0.035);
  osc.connect(amp);
  amp.connect(dest);
  osc.start(when);
  osc.stop(when + 0.04);
  return [osc];
}
