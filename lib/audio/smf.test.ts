import { describe, expect, it } from "vitest";
import { bendValue, encodeMidi, PPQ, vlq } from "./smf";

/** Parse the single track back into events: enough of a reader to check the writer. */
function parse(bytes: Uint8Array) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (o: number) => String.fromCharCode(...bytes.slice(o, o + 4));
  const header = { tag: tag(0), length: view.getUint32(4), format: view.getUint16(8), tracks: view.getUint16(10), division: view.getUint16(12) };
  const trackTag = tag(14);
  const trackLength = view.getUint32(18);
  const events: Array<{ tick: number; status: number; data: number[] }> = [];
  let i = 22;
  let tick = 0;
  while (i < bytes.length) {
    let delta = 0;
    for (;;) {
      const b = bytes[i++];
      delta = (delta << 7) | (b & 0x7f);
      if (!(b & 0x80)) break;
    }
    tick += delta;
    const status = bytes[i++];
    if (status === 0xff) {
      const type = bytes[i++];
      const len = bytes[i++];
      events.push({ tick, status: 0xff, data: [type, ...bytes.slice(i, i + len)] });
      i += len;
    } else {
      events.push({ tick, status, data: [bytes[i], bytes[i + 1]] });
      i += 2;
    }
  }
  return { header, trackTag, trackLength, events };
}

const notes = [
  { midi: 62, detuneCents: 0, startSec: 0, durSec: 0.5, velocity: 0.8 },
  { midi: 65, detuneCents: 50, startSec: 0.5, durSec: 0.5, velocity: 0.8 },
  { midi: 67, detuneCents: -30, startSec: 1, durSec: 0.5, velocity: 0.8 },
  { midi: 69, detuneCents: 0, startSec: 1.5, durSec: 0.5, velocity: 1 },
];

describe("encodeMidi", () => {
  const bytes = encodeMidi(notes, 120);
  const { header, trackTag, trackLength, events } = parse(bytes);

  it("writes a valid format-0 header and one track of the stated length", () => {
    expect(header).toEqual({ tag: "MThd", length: 6, format: 0, tracks: 1, division: PPQ });
    expect(trackTag).toBe("MTrk");
    expect(trackLength).toBe(bytes.length - 22);
    expect(events[events.length - 1]).toMatchObject({ status: 0xff, data: [0x2f] });
  });

  it("writes the tempo", () => {
    const tempo = events.find((e) => e.status === 0xff && e.data[0] === 0x51)!;
    expect(tempo.tick).toBe(0);
    expect((tempo.data[1] << 16) | (tempo.data[2] << 8) | tempo.data[3]).toBe(500000); // 120 BPM
  });

  it("sets the bend range to 2 semitones with RPN 0 on both channels, before any note", () => {
    const firstNoteOn = events.findIndex((e) => (e.status & 0xf0) === 0x90);
    for (const ch of [0, 1]) {
      const cc = events.slice(0, firstNoteOn).filter((e) => e.status === (0xb0 | ch)).map((e) => e.data);
      expect(cc.slice(0, 4)).toEqual([[101, 0], [100, 0], [6, 2], [38, 0]]);
    }
  });

  it("alternates two channels note by note", () => {
    const ons = events.filter((e) => (e.status & 0xf0) === 0x90);
    expect(ons.map((e) => e.status & 0x0f)).toEqual([0, 1, 0, 1]);
    expect(ons.map((e) => e.data[0])).toEqual([62, 65, 67, 69]);
    expect(ons.map((e) => e.tick)).toEqual([0, 480, 960, 1440]);
    expect(ons[3].data[1]).toBe(127);
  });

  it("sends each note's pitch bend on its channel just before its note-on", () => {
    const ons = events.map((e, i) => ({ e, i })).filter(({ e }) => (e.status & 0xf0) === 0x90);
    const bends = ons.map(({ e, i }) => {
      const before = events[i - 1];
      expect(before.status).toBe(0xe0 | (e.status & 0x0f));
      expect(before.tick).toBe(e.tick);
      return before.data[0] | (before.data[1] << 7);
    });
    expect(bends).toEqual([8192, 8192 + 2048, 8192 - 1229, 8192]);
  });

  it("ends every note, and a note-off at the same tick comes before the next bend", () => {
    const offs = events.filter((e) => (e.status & 0xf0) === 0x80);
    expect(offs.map((e) => [e.status & 0x0f, e.data[0], e.tick])).toEqual([[0, 62, 240 * 2], [1, 65, 960], [0, 67, 1440], [1, 69, 1920]]);
    const at960 = events.filter((e) => e.tick === 960).map((e) => e.status & 0xf0);
    expect(at960).toEqual([0x80, 0xe0, 0x90]);
  });

  it("orders notes by time and handles an empty list", () => {
    const shuffled = parse(encodeMidi([notes[2], notes[0]], 120)).events.filter((e) => (e.status & 0xf0) === 0x90);
    expect(shuffled.map((e) => e.data[0])).toEqual([62, 67]);
    const empty = parse(encodeMidi([], 90));
    expect(empty.header.tag).toBe("MThd");
    expect(empty.events.some((e) => (e.status & 0xf0) === 0x90)).toBe(false);
  });
});

describe("helpers", () => {
  it("maps cents to 14-bit bend values with a 2-semitone range", () => {
    expect(bendValue(0)).toBe(8192);
    expect(bendValue(100)).toBe(8192 + 4096);
    expect(bendValue(-200)).toBe(0);
    expect(bendValue(200)).toBe(16383);
  });
  it("encodes variable-length quantities", () => {
    expect(vlq(0)).toEqual([0]);
    expect(vlq(127)).toEqual([0x7f]);
    expect(vlq(128)).toEqual([0x81, 0x00]);
    expect(vlq(480)).toEqual([0x83, 0x60]);
    expect(vlq(0x4000)).toEqual([0x81, 0x80, 0x00]);
  });
});
