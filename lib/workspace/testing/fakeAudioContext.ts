// A stand-in BaseAudioContext for unit tests. It makes no sound: it records what was
// created, connected and scheduled, so tests can assert on the engine's decisions.

export type ParamEvent = { type: "set" | "linear" | "exponential" | "target" | "cancel"; value?: number; time: number };

export class FakeParam {
  value: number;
  events: ParamEvent[] = [];
  constructor(value = 0) {
    this.value = value;
  }
  setValueAtTime(value: number, time: number) {
    this.events.push({ type: "set", value, time });
    return this;
  }
  linearRampToValueAtTime(value: number, time: number) {
    this.events.push({ type: "linear", value, time });
    return this;
  }
  exponentialRampToValueAtTime(value: number, time: number) {
    this.events.push({ type: "exponential", value, time });
    return this;
  }
  setTargetAtTime(value: number, time: number) {
    this.events.push({ type: "target", value, time });
    return this;
  }
  cancelScheduledValues(time: number) {
    this.events.push({ type: "cancel", time });
    return this;
  }
}

export class FakeNode {
  outputs: FakeNode[] = [];
  constructor(readonly kind: string) {}
  connect(dest: FakeNode) {
    this.outputs.push(dest);
    return dest;
  }
  disconnect() {
    this.outputs = [];
  }
}

export class FakeGain extends FakeNode {
  gain = new FakeParam(1);
  constructor() {
    super("gain");
  }
}

export class FakePanner extends FakeNode {
  pan = new FakeParam(0);
  constructor() {
    super("panner");
  }
}

export class FakeAnalyser extends FakeNode {
  fftSize = 2048;
  constructor() {
    super("analyser");
  }
  getFloatTimeDomainData(data: Float32Array) {
    data.fill(0);
  }
}

export class FakeFilter extends FakeNode {
  type = "lowpass";
  frequency = new FakeParam(350);
  Q = new FakeParam(1);
  gain = new FakeParam(0);
  constructor() {
    super("filter");
  }
}

export class FakeSource extends FakeNode {
  buffer: unknown = null;
  playbackRate = new FakeParam(1);
  detune = new FakeParam(0);
  starts: Array<{ when: number; offset: number; duration: number | undefined }> = [];
  stops: Array<number | undefined> = [];
  onended: (() => void) | null = null;
  constructor() {
    super("source");
  }
  start(when = 0, offset = 0, duration?: number) {
    this.starts.push({ when, offset, duration });
  }
  stop(when?: number) {
    this.stops.push(when);
  }
}

export class FakeOscillator extends FakeNode {
  type = "sine";
  frequency = new FakeParam(440);
  detune = new FakeParam(0);
  starts: number[] = [];
  stops: Array<number | undefined> = [];
  constructor() {
    super("oscillator");
  }
  start(when = 0) {
    this.starts.push(when);
  }
  stop(when?: number) {
    this.stops.push(when);
  }
}

/** Just enough of an AudioBuffer: silent unless you write into the channel data. */
export function fakeBuffer(durationSec: number, sampleRate = 44100, channels = 1): AudioBuffer {
  const length = Math.max(1, Math.round(durationSec * sampleRate));
  const data = Array.from({ length: channels }, () => new Float32Array(length));
  return {
    numberOfChannels: channels,
    sampleRate,
    length,
    duration: length / sampleRate,
    getChannelData: (c: number) => data[c],
  } as unknown as AudioBuffer;
}

export class FakeContext {
  currentTime = 0;
  sampleRate: number;
  length: number;
  state = "suspended";
  destination = new FakeNode("destination");
  gains: FakeGain[] = [];
  panners: FakePanner[] = [];
  analysers: FakeAnalyser[] = [];
  sources: FakeSource[] = [];
  oscillators: FakeOscillator[] = [];
  filters: FakeFilter[] = [];
  rendered = false;

  constructor(sampleRate = 44100, length = 0) {
    this.sampleRate = sampleRate;
    this.length = length;
  }

  createGain() {
    const node = new FakeGain();
    this.gains.push(node);
    return node;
  }
  createStereoPanner() {
    const node = new FakePanner();
    this.panners.push(node);
    return node;
  }
  createAnalyser() {
    const node = new FakeAnalyser();
    this.analysers.push(node);
    return node;
  }
  createBufferSource() {
    const node = new FakeSource();
    this.sources.push(node);
    return node;
  }
  createOscillator() {
    const node = new FakeOscillator();
    this.oscillators.push(node);
    return node;
  }
  createBiquadFilter() {
    const node = new FakeFilter();
    this.filters.push(node);
    return node;
  }
  createBuffer(channels: number, length: number, sampleRate: number) {
    return fakeBuffer(length / sampleRate, sampleRate, channels);
  }
  resume() {
    this.state = "running";
    return Promise.resolve();
  }
  close() {
    this.state = "closed";
    return Promise.resolve();
  }
  startRendering() {
    this.rendered = true;
    return Promise.resolve(fakeBuffer(this.length / this.sampleRate, this.sampleRate, 2));
  }

  /** Buffer sources that play a given buffer (clips), leaving out drum noise. */
  sourcesOf(buffer: unknown) {
    return this.sources.filter((s) => s.buffer === buffer);
  }
}

/** The fake as the type the engine expects. */
export const asContext = <T = BaseAudioContext>(ctx: FakeContext) => ctx as unknown as T;
