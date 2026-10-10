// The Workspace audio engine (spec 12.6). One graph builder and one scheduler serve both
// live playback (AudioContext + a lookahead timer) and export (OfflineAudioContext), so
// what you hear is what you bounce.
//
//   source -> [clip gain + fades] -> track gain -> pan -> analyser -> master gain -> analyser -> destination
//   (MIDI notes and drum voices join at the track gain, each behind a small gain of their own)
//
// Project time p maps to context time ``origin + p``. Audio clips start with
// AudioBufferSourceNode.start(when, offset, duration), so starts are sample-accurate.
import {
  effectiveGain,
  type AudioClip,
  type DrumVoice,
  type Project,
} from "../workspace/project";
import { beatsPerBar, secPerBar, secPerBeat } from "../workspace/time";
import { click, DRUM_SOUNDS } from "./drums";
import { midiToHz, playNote } from "./synth";

export type BufferLookup = (assetId: string) => AudioBuffer | undefined;
export type Buffers = ReadonlyMap<string, AudioBuffer> | BufferLookup;

const lookup = (buffers: Buffers): BufferLookup =>
  typeof buffers === "function" ? buffers : (id) => buffers.get(id);

/** Something scheduled that can still be cancelled. ``endsAt`` is a context time. */
export type Handle = { stop(): void; endsAt: number };

type TrackNodes = {
  gain: GainNode;
  panner: StereoPannerNode;
  analyser: AnalyserNode;
  /** Where MIDI notes connect: a fixed make-up gain in front of the track gain. */
  synth?: GainNode;
};
type RowNodes = { gain: GainNode; panner: StereoPannerNode };

export type Graph = {
  ctx: BaseAudioContext;
  master: GainNode;
  masterAnalyser: AnalyserNode;
  tracks: Map<string, TrackNodes>;
  drumRows: Map<DrumVoice, RowNodes>;
  drumTrackId: string | null;
  metronome: GainNode;
};

const ANALYSER_SIZE = 1024;
const MIN_LOOP_SEC = 0.05;
/** The shared note synth is quiet next to audio clips and drums; MIDI tracks get this much make-up gain. */
const SYNTH_GAIN = 2;
const DRUM_LEVEL = 0.8;

function setParam(ctx: BaseAudioContext, param: AudioParam, value: number) {
  // While running, glide to the new value so a fader move doesn't click.
  if (ctx.state === "running" && typeof param.setTargetAtTime === "function") {
    param.setTargetAtTime(value, ctx.currentTime, 0.012);
  } else {
    param.value = value;
  }
}

/** Build the mixer for a project on any context. */
export function buildGraph(ctx: BaseAudioContext, project: Project): Graph {
  const master = ctx.createGain();
  const masterAnalyser = ctx.createAnalyser();
  masterAnalyser.fftSize = ANALYSER_SIZE;
  master.connect(masterAnalyser);
  masterAnalyser.connect(ctx.destination);
  const metronome = ctx.createGain();
  metronome.connect(master);
  const graph: Graph = {
    ctx,
    master,
    masterAnalyser,
    tracks: new Map(),
    drumRows: new Map(),
    drumTrackId: null,
    metronome,
  };
  syncGraph(graph, project);
  return graph;
}

/** Bring the mixer in line with the project: tracks added or removed, gains, pans, mute and solo. */
export function syncGraph(graph: Graph, project: Project): void {
  const { ctx } = graph;
  setParam(ctx, graph.master.gain, project.masterGain);

  const alive = new Set(project.tracks.map((t) => t.id));
  for (const [id, nodes] of graph.tracks) {
    if (alive.has(id)) continue;
    nodes.synth?.disconnect();
    nodes.gain.disconnect();
    nodes.panner.disconnect();
    nodes.analyser.disconnect();
    graph.tracks.delete(id);
  }
  for (const track of project.tracks) {
    let nodes = graph.tracks.get(track.id);
    if (!nodes) {
      nodes = { gain: ctx.createGain(), panner: ctx.createStereoPanner(), analyser: ctx.createAnalyser() };
      nodes.analyser.fftSize = ANALYSER_SIZE;
      nodes.gain.connect(nodes.panner);
      nodes.panner.connect(nodes.analyser);
      nodes.analyser.connect(graph.master);
      if (track.kind === "midi") {
        nodes.synth = ctx.createGain();
        nodes.synth.gain.value = SYNTH_GAIN;
        nodes.synth.connect(nodes.gain);
      }
      graph.tracks.set(track.id, nodes);
    }
    setParam(ctx, nodes.gain.gain, effectiveGain(track, project.tracks));
    setParam(ctx, nodes.panner.pan, track.pan);
  }

  // Drum rows: each voice has its own gain and pan, feeding the drum track.
  const drumTrack = project.drums ? graph.tracks.get(project.drums.trackId) : undefined;
  const drumTrackId = drumTrack && project.drums ? project.drums.trackId : null;
  if (drumTrackId !== graph.drumTrackId) {
    for (const row of graph.drumRows.values()) {
      row.gain.disconnect();
      row.panner.disconnect();
    }
    graph.drumRows.clear();
    graph.drumTrackId = drumTrackId;
  }
  if (project.drums && drumTrack) {
    for (const row of project.drums.rows) {
      let nodes = graph.drumRows.get(row.voice);
      if (!nodes) {
        nodes = { gain: ctx.createGain(), panner: ctx.createStereoPanner() };
        nodes.gain.connect(nodes.panner);
        nodes.panner.connect(drumTrack.gain);
        graph.drumRows.set(row.voice, nodes);
      }
      setParam(ctx, nodes.gain.gain, row.gain);
      setParam(ctx, nodes.panner.pan, row.pan);
    }
  }
}

/** Clip gain with its linear fades, at a time measured from the start of the clip. */
export function clipGainAt(clip: Pick<AudioClip, "gain" | "fadeInSec" | "fadeOutSec" | "lengthSec">, local: number): number {
  let g = clip.gain;
  if (clip.fadeInSec > 0) g *= Math.min(1, Math.max(0, local / clip.fadeInSec));
  if (clip.fadeOutSec > 0) g *= Math.min(1, Math.max(0, (clip.lengthSec - local) / clip.fadeOutSec));
  return g;
}

function stopAll(nodes: AudioScheduledSourceNode[]) {
  for (const node of nodes) {
    try {
      node.stop();
    } catch {
      // already stopped
    }
  }
}

export type Range = {
  /** The context time of project time 0. */
  origin: number;
  /** Schedule whatever starts in [from, to), in project seconds. */
  from: number;
  to: number;
  /** Also start whatever is already sounding at ``from``: the first window after play, seek or a loop wrap. */
  includeRunning: boolean;
  /** Nothing sounds past this project time: the loop end, or the end of an offline render. */
  limitEnd?: number;
  /** Schedule metronome clicks when the project's metronome is on. Off for export. */
  metronome?: boolean;
};

/** Where in [from, to) something that spans [start, end) should begin, or null if it shouldn't. */
function entry(start: number, end: number, r: Range): number | null {
  if (start >= r.from && start < r.to) return start;
  if (r.includeRunning && start < r.from && end > r.from) return r.from;
  return null;
}

/** Schedule one window of the project on a graph. Used tick by tick live, and once for a whole offline render. */
export function scheduleRange(graph: Graph, project: Project, buffers: Buffers, r: Range): Handle[] {
  const { ctx } = graph;
  const getBuffer = lookup(buffers);
  const limit = r.limitEnd ?? Infinity;
  const handles: Handle[] = [];

  for (const clip of project.clips) {
    const track = graph.tracks.get(clip.trackId);
    if (!track) continue;
    const clipEnd = Math.min(clip.startSec + clip.lengthSec, limit);

    if (clip.kind === "audio") {
      const buffer = getBuffer(clip.assetId);
      const begin = entry(clip.startSec, clipEnd, r);
      if (!buffer || begin === null) continue;
      const duration = clipEnd - begin;
      if (duration < 0.001) continue; // a sliver before the loop end would only click
      const local = begin - clip.startSec;
      const src = ctx.createBufferSource();
      const env = ctx.createGain();
      src.buffer = buffer;
      // The envelope is linear between its corners, so ramping corner to corner is exact
      // even when playback starts or stops part-way through a fade.
      const clipAt = r.origin + clip.startSec;
      env.gain.setValueAtTime(clipGainAt(clip, local), clipAt + local);
      const corners = [clip.fadeInSec, clip.lengthSec - clip.fadeOutSec, local + duration]
        .filter((c) => c > local && c <= local + duration)
        .sort((a, b) => a - b);
      for (const corner of corners) env.gain.linearRampToValueAtTime(clipGainAt(clip, corner), clipAt + corner);
      src.connect(env);
      env.connect(track.gain);
      src.start(r.origin + begin, clip.offsetSec + local, duration);
      handles.push({
        endsAt: r.origin + clipEnd,
        stop() {
          stopAll([src]);
          src.disconnect();
          env.disconnect();
        },
      });
      continue;
    }

    for (const note of clip.notes) {
      if (note.startSec < 0 || note.startSec >= clip.lengthSec) continue;
      const start = clip.startSec + note.startSec;
      const end = Math.min(start + note.durSec, clipEnd);
      const begin = entry(start, end, r);
      if (begin === null || end - begin < 0.005) continue;
      const oscillators = playNote(ctx, track.synth ?? track.gain, {
        freqHz: midiToHz(note.midi),
        when: r.origin + begin,
        duration: end - begin,
        velocity: note.velocity,
        detuneCents: note.detuneCents,
      });
      handles.push({ endsAt: r.origin + end + 0.05, stop: () => stopAll(oscillators) });
    }
  }

  const drums = project.drums;
  if (drums && graph.drumRows.size > 0 && drums.steps > 0) {
    const stepSec = secPerBar(project.tempo, project.timeSignature) / drums.steps;
    for (const [k, when] of gridTimes(stepSec, r)) {
      for (const row of drums.rows) {
        const nodes = graph.drumRows.get(row.voice);
        if (!nodes || !row.steps[k % drums.steps]) continue;
        const sources = DRUM_SOUNDS[row.voice](ctx, nodes.gain, when, DRUM_LEVEL);
        handles.push({ endsAt: when + 0.4, stop: () => stopAll(sources) });
      }
    }
  }

  if (r.metronome && project.metronome) {
    const perBar = beatsPerBar(project.timeSignature);
    for (const [k, when] of gridTimes(secPerBeat(project.tempo), r)) {
      const accent = Number.isInteger(perBar) ? k % perBar === 0 : k === 0;
      const sources = click(ctx, graph.metronome, when, accent);
      handles.push({ endsAt: when + 0.05, stop: () => stopAll(sources) });
    }
  }

  return handles;
}

/** Grid lines closer than this to a window edge count as on it. */
const EDGE = 1e-4;

/**
 * The grid lines (drum steps, metronome beats) that fall in a window, as [index, context
 * time]. Loop points and seek positions are often a grid line give or take float
 * rounding, so the window is taken as [from - EDGE, end - EDGE): the line on the start
 * edge is in, the line on the end edge belongs to the next window (or the next lap).
 * Consecutive windows still share no line.
 */
function gridTimes(stepSec: number, r: Range): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  if (!(stepSec > 0)) return out;
  const end = Math.min(r.to, r.limitEnd ?? Infinity) - EDGE;
  for (let k = Math.max(0, Math.floor((r.from - EDGE) / stepSec)); ; k++) {
    const t = k * stepSec;
    if (t >= end) break;
    if (t >= r.from - EDGE) out.push([k, Math.max(0, r.origin + t)]);
  }
  return out;
}

// --- offline -----------------------------------------------------------------

export type RenderOptions = {
  startSec: number;
  endSec: number;
  sampleRate?: number;
  /** For tests: supply the context instead of ``new OfflineAudioContext``. */
  createContext?: (channels: number, length: number, sampleRate: number) => OfflineAudioContext;
};

/** Render a stretch of the project to a stereo buffer. Everything is scheduled up front. */
export async function renderOffline(project: Project, buffers: Buffers, opts: RenderOptions): Promise<AudioBuffer> {
  const sampleRate = opts.sampleRate ?? 44100;
  const startSec = Math.max(0, opts.startSec);
  const endSec = Math.max(startSec, opts.endSec);
  const length = Math.max(1, Math.ceil((endSec - startSec) * sampleRate));
  const ctx = opts.createContext
    ? opts.createContext(2, length, sampleRate)
    : new OfflineAudioContext(2, length, sampleRate);
  const graph = buildGraph(ctx, project);
  scheduleRange(graph, project, buffers, {
    origin: -startSec,
    from: startSec,
    to: endSec,
    includeRunning: true,
    limitEnd: endSec,
    metronome: false,
  });
  return ctx.startRendering();
}

// --- live transport ----------------------------------------------------------

type Timers = { set(fn: () => void, ms: number): unknown; clear(id: unknown): void };

export type TransportOptions = {
  getProject: () => Project;
  getBuffer: BufferLookup;
  /** Defaults to ``new AudioContext()``. Called on the first play, inside the user gesture. */
  createContext?: () => AudioContext;
  /** How far ahead of the clock to schedule, and how often to look. */
  lookaheadSec?: number;
  intervalMs?: number;
  timers?: Timers;
};

const defaultTimers: Timers = {
  set: (fn, ms) => setInterval(fn, ms),
  clear: (id) => clearInterval(id as ReturnType<typeof setInterval>),
};

/** A short lead so the very first sources are never scheduled in the past. */
const START_DELAY = 0.03;

export class Transport {
  ctx: AudioContext | null = null;
  graph: Graph | null = null;
  playing = false;

  private readonly opts: TransportOptions;
  private readonly lookahead: number;
  private readonly interval: number;
  private readonly timers: Timers;
  private timer: unknown = null;
  private pausedAt = 0;
  private origin = 0;
  /** Origins by the context time they take effect; more than one while a loop wrap is still ahead. */
  private anchors: Array<{ at: number; origin: number; floor: number }> = [];
  private scheduledUntil = 0;
  private includeRunning = false;
  private handles: Handle[] = [];
  private scheduledFor: Project | null = null;
  private listeners = new Set<() => void>();
  private meterData: Float32Array<ArrayBuffer> | null = null;

  constructor(opts: TransportOptions) {
    this.opts = opts;
    this.lookahead = opts.lookaheadSec ?? 0.12;
    this.interval = opts.intervalMs ?? 25;
    this.timers = opts.timers ?? defaultTimers;
  }

  /** Subscribe to play, pause, stop and seek. */
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  private notify() {
    this.listeners.forEach((fn) => fn());
  }

  /** Create the AudioContext and the mixer. Call from a user gesture. */
  ensureContext(): AudioContext {
    if (!this.ctx) {
      this.ctx = this.opts.createContext ? this.opts.createContext() : new AudioContext();
      this.graph = buildGraph(this.ctx, this.opts.getProject());
    }
    return this.ctx;
  }

  /** The playhead, in project seconds. */
  get position(): number {
    if (!this.playing || !this.ctx) return this.pausedAt;
    const now = this.ctx.currentTime;
    while (this.anchors.length > 1 && this.anchors[1].at <= now) this.anchors.shift();
    const anchor = this.anchors[0];
    return anchor ? Math.max(anchor.floor, now - anchor.origin) : this.pausedAt;
  }

  play(fromSec?: number): void {
    const ctx = this.ensureContext();
    if (ctx.state === "suspended") void ctx.resume();
    if (this.playing) {
      if (fromSec !== undefined) this.seek(fromSec);
      return;
    }
    if (fromSec !== undefined) this.pausedAt = Math.max(0, fromSec);
    this.playing = true;
    this.startAt(this.pausedAt, START_DELAY);
    if (this.timer === null) this.timer = this.timers.set(() => this.tick(), this.interval);
    this.notify();
  }

  pause(): void {
    if (!this.playing) return;
    this.pausedAt = this.position;
    this.halt();
  }

  stop(): void {
    this.pausedAt = 0;
    this.halt();
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(sec: number): void {
    const to = Math.max(0, sec);
    this.pausedAt = to;
    if (this.playing) {
      this.clearScheduled();
      this.startAt(to, START_DELAY);
    }
    this.notify();
  }

  /**
   * Call when the project changes. Mixer values apply at once. A change to what is
   * scheduled (clips, loop, tempo) restarts scheduling from the playhead, unless
   * ``defer`` is set (mid-drag), in which case it happens on the next call without it.
   */
  sync(defer = false): void {
    const project = this.opts.getProject();
    if (this.graph) syncGraph(this.graph, project);
    if (defer || !this.playing) return;
    const last = this.scheduledFor;
    this.scheduledFor = project;
    if (
      last &&
      (last.clips !== project.clips ||
        last.loop !== project.loop ||
        last.tempo !== project.tempo ||
        last.timeSignature !== project.timeSignature)
    ) {
      this.refresh();
    }
  }

  /** Restart scheduling from the playhead, e.g. when a clip's audio has just finished loading. */
  refresh(): void {
    if (!this.playing) return;
    const at = this.position;
    this.clearScheduled();
    this.startAt(at, 0.005);
  }

  /** One pass of the lookahead scheduler: schedule everything that starts before now + lookahead. */
  tick(): void {
    const { ctx, graph } = this;
    if (!this.playing || !ctx || !graph) return;
    const project = this.opts.getProject();
    const now = ctx.currentTime;
    if (this.handles.length > 64) this.handles = this.handles.filter((h) => h.endsAt > now);
    const horizon = now + this.lookahead;

    for (let guard = 0; guard < 64; guard++) {
      const target = horizon - this.origin;
      if (target <= this.scheduledUntil) break;
      const loop = project.loop;
      const looping =
        loop.enabled && loop.endSec - loop.startSec >= MIN_LOOP_SEC && this.scheduledUntil < loop.endSec;
      const to = looping ? Math.min(target, loop.endSec) : target;
      this.handles.push(
        ...scheduleRange(graph, project, this.opts.getBuffer, {
          origin: this.origin,
          from: this.scheduledUntil,
          to,
          includeRunning: this.includeRunning,
          limitEnd: looping ? loop.endSec : undefined,
          metronome: true,
        }),
      );
      this.includeRunning = false;
      this.scheduledUntil = to;
      if (!looping || to < loop.endSec) break;
      // Reached the loop end: from that moment, project time jumps back to the loop start.
      const wrapAt = this.origin + loop.endSec;
      this.origin = wrapAt - loop.startSec;
      this.anchors.push({ at: wrapAt, origin: this.origin, floor: loop.startSec });
      this.scheduledUntil = loop.startSec;
      this.includeRunning = true;
    }
  }

  /** Sound one note on a MIDI track right now: feedback while drawing in the piano roll. */
  auditionNote(trackId: string, midi: number, detuneCents = 0, velocity = 0.7): void {
    const track = this.auditionGraph()?.tracks.get(trackId);
    if (!track || !this.ctx) return;
    playNote(this.ctx, track.synth ?? track.gain, {
      freqHz: midiToHz(midi),
      when: this.ctx.currentTime + 0.01,
      duration: 0.3,
      velocity,
      detuneCents,
    });
  }

  /** Sound one drum voice right now: feedback when a step is switched on. */
  auditionDrum(voice: DrumVoice): void {
    const row = this.auditionGraph()?.drumRows.get(voice);
    if (!row || !this.ctx) return;
    DRUM_SOUNDS[voice](this.ctx, row.gain, this.ctx.currentTime + 0.01, DRUM_LEVEL);
  }

  /** The live graph, up to date, with the context awake. Call from a user gesture. */
  private auditionGraph(): Graph | null {
    try {
      const ctx = this.ensureContext();
      if (ctx.state === "suspended") void ctx.resume();
    } catch {
      return null; // no Web Audio here: stay silent rather than break editing
    }
    if (this.graph) syncGraph(this.graph, this.opts.getProject());
    return this.graph;
  }

  /** Peak level of a track, or of the master, from 0 to 1. For meters. */
  level(id: string): number {
    const analyser = id === "master" ? this.graph?.masterAnalyser : this.graph?.tracks.get(id)?.analyser;
    if (!analyser || !this.playing) return 0;
    if (!this.meterData || this.meterData.length !== analyser.fftSize) {
      this.meterData = new Float32Array(analyser.fftSize);
    }
    analyser.getFloatTimeDomainData(this.meterData);
    let peak = 0;
    for (let i = 0; i < this.meterData.length; i++) {
      const v = Math.abs(this.meterData[i]);
      if (v > peak) peak = v;
    }
    return Math.min(1, peak);
  }

  /** Stop and release the AudioContext. The transport can be used again; play makes a new one. */
  dispose(): void {
    this.halt();
    void this.ctx?.close?.();
    this.ctx = null;
    this.graph = null;
  }

  private startAt(position: number, delay: number) {
    const ctx = this.ctx!;
    this.origin = ctx.currentTime + delay - position;
    this.anchors = [{ at: ctx.currentTime, origin: this.origin, floor: position }];
    this.scheduledUntil = position;
    this.includeRunning = true;
    this.scheduledFor = this.opts.getProject();
    if (this.graph) syncGraph(this.graph, this.scheduledFor);
    this.tick();
  }

  private clearScheduled() {
    for (const handle of this.handles) handle.stop();
    this.handles = [];
  }

  private halt() {
    this.clearScheduled();
    if (this.timer !== null) this.timers.clear(this.timer);
    this.timer = null;
    this.playing = false;
    this.notify();
  }
}
