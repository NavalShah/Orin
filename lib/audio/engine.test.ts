import { describe, expect, it } from "vitest";
import { newProject, type AudioClip, type MidiClip, type Project } from "../workspace/project";
import { reduce, type Action } from "../workspace/reducer";
import { asContext, FakeContext, fakeBuffer } from "../workspace/testing/fakeAudioContext";
import { buildGraph, clipGainAt, renderOffline, scheduleRange, syncGraph, Transport } from "./engine";

const run = (p: Project, ...actions: Action[]) => actions.reduce(reduce, p);

const audioClip = (over: Partial<AudioClip> = {}): AudioClip => ({
  id: "c1", trackId: "a1", kind: "audio", assetId: "asset1", name: "Voice",
  startSec: 1, offsetSec: 0.5, lengthSec: 4, gain: 1, fadeInSec: 0, fadeOutSec: 0, ...over,
});
const midiClip = (over: Partial<MidiClip> = {}): MidiClip => ({
  id: "m1", trackId: "k1", kind: "midi", name: "Melody", startSec: 0, lengthSec: 4, tonicHz: null,
  notes: [
    { id: "n1", midi: 69, detuneCents: 0, startSec: 0, durSec: 1, velocity: 0.7 },
    { id: "n2", midi: 53, detuneCents: 50, startSec: 1, durSec: 1, velocity: 0.7 },
    { id: "n3", midi: 60, detuneCents: 0, startSec: 3, durSec: 1, velocity: 0.7 },
  ],
  ...over,
});

function project(...actions: Action[]): Project {
  return run(
    newProject(),
    { type: "addTrack", kind: "audio", id: "a1" },
    { type: "addTrack", kind: "audio", id: "a2" },
    { type: "addTrack", kind: "midi", id: "k1" },
    ...actions,
  );
}

const buffer = fakeBuffer(10);
const buffers = new Map([["asset1", buffer]]);
const manualTimers = { set: () => 1, clear: () => undefined };

function liveTransport(getProject: () => Project) {
  const ctx = new FakeContext();
  ctx.state = "running";
  const transport = new Transport({
    getProject,
    getBuffer: (id) => buffers.get(id),
    createContext: () => asContext<AudioContext>(ctx),
    timers: manualTimers,
  });
  return { ctx, transport };
}

describe("graph", () => {
  it("routes each track through gain, pan and an analyser into the master chain", () => {
    const ctx = new FakeContext();
    const graph = buildGraph(asContext(ctx), project());
    const track = graph.tracks.get("a1")!;
    const out = (node: unknown) => (node as { outputs: unknown[] }).outputs;
    expect(out(track.gain)).toEqual([track.panner]);
    expect(out(track.panner)).toEqual([track.analyser]);
    expect(out(track.analyser)).toEqual([graph.master]);
    expect(out(graph.master)).toEqual([graph.masterAnalyser]);
    expect(out(graph.masterAnalyser)).toEqual([ctx.destination]);
    expect(graph.master.gain.value).toBe(0.8);
  });

  it("gives a muted track gain 0, and solo silences the others", () => {
    const muted = project({ type: "setTrackMute", trackId: "a1", mute: true }, { type: "setTrackPan", trackId: "a2", pan: -0.5 });
    const graph = buildGraph(asContext(new FakeContext()), muted);
    expect(graph.tracks.get("a1")!.gain.gain.value).toBe(0);
    expect(graph.tracks.get("a2")!.gain.gain.value).toBe(0.8);
    expect(graph.tracks.get("a2")!.panner.pan.value).toBe(-0.5);

    syncGraph(graph, run(muted, { type: "setTrackMute", trackId: "a1", mute: false }, { type: "setTrackSolo", trackId: "k1", solo: true }));
    expect(graph.tracks.get("a1")!.gain.gain.value).toBe(0);
    expect(graph.tracks.get("a2")!.gain.gain.value).toBe(0);
    expect(graph.tracks.get("k1")!.gain.gain.value).toBe(0.8);
  });

  it("a muted track still schedules its clip, into a gain of 0", () => {
    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip: audioClip() }, { type: "setTrackMute", trackId: "a1", mute: true });
    const graph = buildGraph(asContext(ctx), p);
    scheduleRange(graph, p, buffers, { origin: 0, from: 0, to: 10, includeRunning: true });
    const [src] = ctx.sourcesOf(buffer);
    const env = src.outputs[0] as unknown as { outputs: unknown[] };
    expect(env.outputs[0]).toBe(graph.tracks.get("a1")!.gain);
    expect(graph.tracks.get("a1")!.gain.gain.value).toBe(0);
  });

  it("adds and removes track nodes as the project changes", () => {
    const graph = buildGraph(asContext(new FakeContext()), project());
    syncGraph(graph, run(project(), { type: "removeTrack", trackId: "a2" }, { type: "addTrack", kind: "drums", id: "d1" }));
    expect([...graph.tracks.keys()]).toEqual(["a1", "k1", "d1"]);
    expect(graph.drumRows.size).toBe(4);
  });
});

describe("scheduleRange", () => {
  it("starts a clip that begins inside the window at its own start", () => {
    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip: audioClip() });
    scheduleRange(buildGraph(asContext(ctx), p), p, buffers, { origin: 100, from: 0.95, to: 1.07, includeRunning: false });
    expect(ctx.sourcesOf(buffer)[0].starts).toEqual([{ when: 101, offset: 0.5, duration: 4 }]);
  });

  it("starts a clip that began before the play position with the right offset and remaining duration", () => {
    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip: audioClip() }); // 1 s .. 5 s, offset 0.5
    // Play from 2 s; project time 2 is context time 10.
    scheduleRange(buildGraph(asContext(ctx), p), p, buffers, { origin: 8, from: 2, to: 2.12, includeRunning: true });
    expect(ctx.sourcesOf(buffer)[0].starts).toEqual([{ when: 10, offset: 1.5, duration: 3 }]);
  });

  it("leaves a running clip alone on later windows, and skips clips outside the window", () => {
    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip: audioClip() });
    const graph = buildGraph(asContext(ctx), p);
    scheduleRange(graph, p, buffers, { origin: 0, from: 2, to: 2.12, includeRunning: false });
    scheduleRange(graph, p, buffers, { origin: 0, from: 6, to: 6.12, includeRunning: true });
    expect(ctx.sourcesOf(buffer)).toHaveLength(0);
  });

  it("applies clip gain and linear fades, also when starting inside a fade", () => {
    const clip = audioClip({ startSec: 0, offsetSec: 0, lengthSec: 4, gain: 0.5, fadeInSec: 1, fadeOutSec: 2 });
    expect(clipGainAt(clip, 0)).toBe(0);
    expect(clipGainAt(clip, 0.5)).toBe(0.25);
    expect(clipGainAt(clip, 1.5)).toBe(0.5);
    expect(clipGainAt(clip, 3)).toBe(0.25);
    expect(clipGainAt(clip, 4)).toBe(0);

    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip });
    scheduleRange(buildGraph(asContext(ctx), p), p, buffers, { origin: 10, from: 0.5, to: 0.6, includeRunning: true });
    const env = ctx.sourcesOf(buffer)[0].outputs[0] as unknown as { gain: { events: unknown[] } };
    expect(env.gain.events).toEqual([
      { type: "set", value: 0.25, time: 10.5 },
      { type: "linear", value: 0.5, time: 11 },
      { type: "linear", value: 0.5, time: 12 },
      { type: "linear", value: 0, time: 14 },
    ]);
  });

  it("plays MIDI notes through the synth with their detune", () => {
    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip: midiClip({ startSec: 2 }) });
    scheduleRange(buildGraph(asContext(ctx), p), p, buffers, { origin: 0, from: 0, to: 10, includeRunning: false });
    // The synth uses two oscillators per note.
    expect(ctx.oscillators).toHaveLength(6);
    expect(ctx.oscillators[0].frequency.value).toBeCloseTo(440);
    expect(ctx.oscillators[0].starts).toEqual([2]);
    expect(ctx.oscillators[2].detune.value).toBe(50);
    expect(ctx.oscillators[2].starts).toEqual([3]);
  });

  it("feeds MIDI notes to their track through a make-up gain", () => {
    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip: midiClip() });
    const graph = buildGraph(asContext(ctx), p);
    const track = graph.tracks.get("k1")!;
    expect(track.synth).toBeDefined();
    expect((track.synth as unknown as { outputs: unknown[] }).outputs).toEqual([track.gain]);
    expect(graph.tracks.get("a1")!.synth).toBeUndefined();
    scheduleRange(graph, p, buffers, { origin: 0, from: 0, to: 1, includeRunning: false });
    // oscillator -> voice gain -> note envelope -> the track's synth input
    const envelope = (ctx.oscillators[0].outputs[0] as unknown as { outputs: Array<{ outputs: unknown[] }> }).outputs[0];
    expect(envelope.outputs[0]).toBe(track.synth);
  });

  it("does not play notes hidden past the end of a trimmed MIDI clip", () => {
    const ctx = new FakeContext();
    const p = project({ type: "addClip", clip: midiClip({ lengthSec: 1.5 }) });
    scheduleRange(buildGraph(asContext(ctx), p), p, buffers, { origin: 0, from: 0, to: 10, includeRunning: false });
    expect(ctx.oscillators).toHaveLength(4); // n1 and n2; n3 starts after the clip ends
    // n2 runs 1..2 but the clip ends at 1.5: the synth stops it 0.02 s after its end.
    expect(ctx.oscillators[2].stops[0]).toBeCloseTo(1.52);
  });

  it("loops the drum pattern with the bar", () => {
    const ctx = new FakeContext();
    const p = run(project(), { type: "addTrack", kind: "drums", id: "d1" }, { type: "toggleStep", voice: "kick", step: 0 }, { type: "toggleStep", voice: "kick", step: 8 });
    // 120 BPM in 4/4: a bar is 2 s, a step 0.125 s. Two bars = four kicks.
    scheduleRange(buildGraph(asContext(ctx), p), p, buffers, { origin: 0, from: 0, to: 4, includeRunning: false });
    expect(ctx.oscillators.map((o) => o.starts[0])).toEqual([0, 1, 2, 3]);
  });

  it("handles loop points that are a grid line give or take float rounding", () => {
    // 90 BPM: a bar is 2.666... s and a step 0.1666... s, neither exact in binary.
    const p = run(project(), { type: "setTempo", tempo: 90 }, { type: "addTrack", kind: "drums", id: "d1" }, { type: "toggleStep", voice: "kick", step: 0 });
    const bar = (60 / 90) * 4;
    const kicks = (from: number, to: number, limitEnd?: number) => {
      const ctx = new FakeContext();
      scheduleRange(buildGraph(asContext(ctx), p), p, buffers, { origin: 10, from, to, includeRunning: true, limitEnd });
      return ctx.oscillators.map((o) => o.starts[0] - 10);
    };
    // Starting on bar 3, whether the position was rounded down or up: the downbeat plays.
    expect(kicks(5.333333, 5.4)).toHaveLength(1);
    expect(kicks(5.333334, 5.4)).toHaveLength(1);
    expect(kicks(2 * bar, 5.4)).toHaveLength(1);
    // Ending on bar 2, rounded either way: the next downbeat belongs to the next lap.
    expect(kicks(0, 3, 2.666667)).toHaveLength(1);
    expect(kicks(0, 3, 2.666666)).toHaveLength(1);
    expect(kicks(0, 3, bar)).toHaveLength(1);
    // Windows that meet on a bar line play its downbeat once.
    expect([...kicks(2.5, 2.666667), ...kicks(2.666667, 2.8)]).toHaveLength(1);
    expect([...kicks(2.5, bar), ...kicks(bar, 2.8)]).toHaveLength(1);
  });

  it("never schedules before time zero in an offline render", async () => {
    const p = run(project(), { type: "setTempo", tempo: 90 }, { type: "addTrack", kind: "drums", id: "d1" }, { type: "toggleStep", voice: "hat", step: 0 });
    let ctx!: FakeContext;
    await renderOffline(p, buffers, {
      startSec: 5.333334, // a hair after bar 3
      endSec: 6,
      createContext: (_c, length, sampleRate) => (ctx = new FakeContext(sampleRate, length)) && asContext<OfflineAudioContext>(ctx),
    });
    const hits = ctx.sources.filter((s) => s.starts.length > 0);
    expect(hits).toHaveLength(1);
    expect(hits[0].starts[0].when).toBe(0);
  });

  it("clicks the metronome only when asked and switched on", () => {
    const on = run(project(), { type: "setMetronome", on: true });
    const quiet = new FakeContext();
    scheduleRange(buildGraph(asContext(quiet), on), on, buffers, { origin: 0, from: 0, to: 2, includeRunning: false });
    expect(quiet.oscillators).toHaveLength(0);
    const ctx = new FakeContext();
    scheduleRange(buildGraph(asContext(ctx), on), on, buffers, { origin: 0, from: 0, to: 2, includeRunning: false, metronome: true });
    expect(ctx.oscillators.map((o) => o.starts[0])).toEqual([0, 0.5, 1, 1.5]);
    expect(ctx.oscillators[0].frequency.value).toBeGreaterThan(ctx.oscillators[1].frequency.value); // bar accent
  });
});

describe("Transport", () => {
  it("creates the context only on play", () => {
    const p = project();
    const { transport } = liveTransport(() => p);
    expect(transport.ctx).toBeNull();
    transport.play();
    expect(transport.ctx).not.toBeNull();
    expect(transport.playing).toBe(true);
  });

  it("plays from any position: a clip already running starts part-way in", () => {
    const p = project({ type: "addClip", clip: audioClip() });
    const { ctx, transport } = liveTransport(() => p);
    ctx.currentTime = 5;
    transport.play(2);
    const [start] = ctx.sourcesOf(buffer)[0].starts;
    expect(start.when).toBeCloseTo(5.03);
    expect(start.offset).toBeCloseTo(1.5);
    expect(start.duration).toBeCloseTo(3);
    expect(transport.position).toBe(2);
  });

  it("schedules ahead of the clock in small windows, each thing once", () => {
    const p = project({ type: "addClip", clip: audioClip({ startSec: 0.5 }) });
    const { ctx, transport } = liveTransport(() => p);
    transport.play(0);
    expect(ctx.sourcesOf(buffer)).toHaveLength(0); // 0.5 s is beyond the 120 ms lookahead
    for (let t = 0.025; t < 1; t += 0.025) {
      ctx.currentTime = t;
      transport.tick();
    }
    expect(ctx.sourcesOf(buffer)).toHaveLength(1);
    expect(ctx.sourcesOf(buffer)[0].starts[0].when).toBeCloseTo(0.53);
    expect(transport.position).toBeCloseTo(0.975 - 0.03);
  });

  it("pause keeps the position, stop returns to the start, and both stop what is sounding", () => {
    const p = project({ type: "addClip", clip: audioClip({ startSec: 0 }) });
    const { ctx, transport } = liveTransport(() => p);
    transport.play(0);
    ctx.currentTime = 1.03;
    transport.pause();
    expect(transport.playing).toBe(false);
    expect(transport.position).toBeCloseTo(1);
    expect(ctx.sourcesOf(buffer)[0].stops).toHaveLength(1);
    transport.play();
    expect(ctx.sourcesOf(buffer)[1].starts[0].offset).toBeCloseTo(1.5);
    transport.stop();
    expect(transport.position).toBe(0);
    expect(ctx.sourcesOf(buffer)[1].stops).toHaveLength(1);
  });

  it("seek while playing restarts the sources from the new position", () => {
    const p = project({ type: "addClip", clip: audioClip({ startSec: 0, offsetSec: 0, lengthSec: 8 }) });
    const { ctx, transport } = liveTransport(() => p);
    transport.play(0);
    ctx.currentTime = 1;
    transport.seek(6);
    const sources = ctx.sourcesOf(buffer);
    expect(sources[0].stops).toHaveLength(1);
    expect(sources[1].starts[0]).toMatchObject({ offset: 6 });
    expect(sources[1].starts[0].duration).toBeCloseTo(2);
    expect(transport.position).toBeCloseTo(6);
  });

  it("wraps at the loop end and cuts sources that would run past it", () => {
    const p = project(
      { type: "addClip", clip: audioClip({ startSec: 0, offsetSec: 0, lengthSec: 8 }) },
      { type: "setLoop", loop: { enabled: true, startSec: 0, endSec: 1 } },
    );
    const { ctx, transport } = liveTransport(() => p);
    transport.play(0.9);
    const sources = ctx.sourcesOf(buffer);
    // The clip is already running at 0.9 s: it starts part-way in and is cut at the loop end.
    expect(sources).toHaveLength(1);
    expect(sources[0].starts[0].when).toBeCloseTo(0.03);
    expect(sources[0].starts[0].offset).toBeCloseTo(0.9);
    expect(sources[0].starts[0].duration).toBeCloseTo(0.1);
    expect(transport.position).toBeCloseTo(0.9);

    // Once the loop end comes inside the lookahead, the next lap is scheduled from the
    // loop start, to begin exactly when the first one ends.
    ctx.currentTime = 0.05;
    transport.tick();
    expect(sources.length).toBe(1);
    const lap2 = ctx.sourcesOf(buffer)[1];
    expect(lap2.starts[0].when).toBeCloseTo(0.13);
    expect(lap2.starts[0].offset).toBeCloseTo(0);
    expect(lap2.starts[0].duration).toBeCloseTo(1);
    expect(transport.position).toBeCloseTo(0.92); // the playhead has not wrapped yet

    ctx.currentTime = 0.2;
    transport.tick();
    expect(transport.position).toBeCloseTo(0.07); // wrapped: 0.07 s into the next lap
    expect(ctx.sourcesOf(buffer)).toHaveLength(2); // nothing scheduled twice

    // Laps keep coming, one source per lap, each a loop length apart.
    for (let t = 0.225; t < 3.2; t += 0.025) {
      ctx.currentTime = t;
      transport.tick();
    }
    const starts = ctx.sourcesOf(buffer).map((s) => s.starts[0].when);
    expect(starts).toHaveLength(5);
    expect(starts[2] - starts[1]).toBeCloseTo(1);
    expect(starts[4] - starts[3]).toBeCloseTo(1);
    expect(transport.position).toBeLessThan(1);
  });

  it("plays straight through when the playhead starts after the loop", () => {
    const p = project(
      { type: "addClip", clip: audioClip({ startSec: 0, offsetSec: 0, lengthSec: 8 }) },
      { type: "setLoop", loop: { enabled: true, startSec: 0, endSec: 1 } },
    );
    const { ctx, transport } = liveTransport(() => p);
    transport.play(2);
    expect(ctx.sourcesOf(buffer)[0].starts[0].duration).toBeCloseTo(6);
  });

  it("reschedules when clips change during playback, but not for a mixer move", () => {
    let p = project({ type: "addClip", clip: audioClip({ startSec: 0, offsetSec: 0, lengthSec: 8 }) });
    const { ctx, transport } = liveTransport(() => p);
    transport.play(0);
    ctx.currentTime = 1;
    p = reduce(p, { type: "setTrackGain", trackId: "a1", gain: 0.2 });
    transport.sync();
    expect(ctx.sourcesOf(buffer)).toHaveLength(1);
    expect(transport.graph!.tracks.get("a1")!.gain.gain.events.at(-1)).toMatchObject({ type: "target", value: 0.2 });

    p = reduce(p, { type: "moveClip", clipId: "c1", startSec: 0.5 });
    transport.sync(true); // mid-drag: wait
    expect(ctx.sourcesOf(buffer)).toHaveLength(1);
    transport.sync();
    const sources = ctx.sourcesOf(buffer);
    expect(sources).toHaveLength(2);
    expect(sources[0].stops).toHaveLength(1);
    expect(sources[1].starts[0].offset).toBeCloseTo(0.47, 1);
  });

  it("auditions a note and a drum voice without starting playback", () => {
    const p = run(project(), { type: "addTrack", kind: "drums", id: "d1" });
    const { ctx, transport } = liveTransport(() => p);
    ctx.currentTime = 4;
    transport.auditionNote("k1", 53, 50);
    expect(transport.playing).toBe(false);
    expect(ctx.oscillators).toHaveLength(2);
    expect(ctx.oscillators[0].detune.value).toBe(50);
    expect(ctx.oscillators[0].starts[0]).toBeCloseTo(4.01);
    transport.auditionDrum("kick");
    expect(ctx.oscillators).toHaveLength(3);
    transport.auditionNote("missing", 60); // unknown track: nothing happens
    expect(ctx.oscillators).toHaveLength(3);
  });

  it("tells subscribers about play, pause and seek", () => {
    const p = project();
    const { transport } = liveTransport(() => p);
    let calls = 0;
    transport.subscribe(() => calls++);
    transport.play();
    transport.seek(3);
    transport.pause();
    expect(calls).toBe(3);
  });
});

describe("renderOffline", () => {
  const p = project(
    { type: "addClip", clip: audioClip({ id: "early", startSec: 0, offsetSec: 0, lengthSec: 1 }) },
    { type: "addClip", clip: audioClip({ id: "across", startSec: 1, offsetSec: 0.5, lengthSec: 4 }) },
    { type: "addClip", clip: audioClip({ id: "inside", trackId: "a2", startSec: 3, offsetSec: 0, lengthSec: 1 }) },
    { type: "addClip", clip: audioClip({ id: "late", startSec: 9, offsetSec: 0, lengthSec: 1 }) },
    { type: "addClip", clip: midiClip({ startSec: 2 }) }, // notes at 2, 3 and 5 s
  );

  it("schedules every clip and note inside the range, and nothing outside it", async () => {
    let ctx!: FakeContext;
    const out = await renderOffline(p, buffers, {
      startSec: 2,
      endSec: 4.5,
      sampleRate: 48000,
      createContext: (channels, length, sampleRate) => {
        expect(channels).toBe(2);
        expect(length).toBe(2.5 * 48000);
        ctx = new FakeContext(sampleRate, length);
        return asContext<OfflineAudioContext>(ctx);
      },
    });
    expect(ctx.rendered).toBe(true);
    expect(out.length).toBe(2.5 * 48000);

    // "across" is already running at 2 s; "inside" starts at 3 s. Context time 0 is project time 2.
    const starts = ctx.sourcesOf(buffer).map((s) => s.starts[0]);
    expect(starts).toHaveLength(2);
    expect(starts[0].when).toBeCloseTo(0);
    expect(starts[0].offset).toBeCloseTo(1.5);
    expect(starts[0].duration).toBeCloseTo(2.5); // cut at the end of the render
    expect(starts[1]).toEqual({ when: 1, offset: 0, duration: 1 });

    // Notes at 2 s and 3 s are in range; the one at 5 s is not. Two oscillators per note.
    expect(ctx.oscillators.map((o) => o.starts[0])).toEqual([0, 0, 1, 1]);
    expect(ctx.oscillators[2].detune.value).toBe(50);
  });

  it("renders the whole project when the range covers it", async () => {
    let ctx!: FakeContext;
    await renderOffline(p, (id) => buffers.get(id), {
      startSec: 0,
      endSec: 10,
      createContext: (_c, length, sampleRate) => (ctx = new FakeContext(sampleRate, length)) && asContext<OfflineAudioContext>(ctx),
    });
    expect(ctx.sourcesOf(buffer)).toHaveLength(4);
    expect(ctx.oscillators).toHaveLength(6);
    expect(ctx.sampleRate).toBe(44100);
  });

  it("honours mute in the mixdown and skips clips whose audio is missing", async () => {
    let ctx!: FakeContext;
    const muted = run(p, { type: "setTrackMute", trackId: "k1", mute: true }, { type: "addClip", clip: audioClip({ id: "ghost", assetId: "missing" }) });
    await renderOffline(muted, buffers, {
      startSec: 0,
      endSec: 10,
      createContext: (_c, length, sampleRate) => (ctx = new FakeContext(sampleRate, length)) && asContext<OfflineAudioContext>(ctx),
    });
    expect(ctx.sourcesOf(buffer)).toHaveLength(4);
    // gains[0] is the master, [1] the metronome bus; then one per track in order.
    const trackGains = ctx.gains.slice(2, 5).map((g) => g.gain.value);
    expect(trackGains).toEqual([0.8, 0.8, 0]);
  });
});
