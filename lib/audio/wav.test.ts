import { describe, expect, it } from "vitest";
import { fakeBuffer } from "../workspace/testing/fakeAudioContext";
import { encodeWav, encodeWavBytes } from "./wav";

const text = (bytes: Uint8Array, from: number, to: number) => String.fromCharCode(...bytes.slice(from, to));

describe("encodeWav", () => {
  it("writes a 16-bit PCM header and interleaved samples for a short stereo buffer", () => {
    const buffer = fakeBuffer(0.01, 48000, 2); // 480 frames
    const left = buffer.getChannelData(0);
    const right = buffer.getChannelData(1);
    left[0] = 1;
    right[0] = -1;
    left[1] = 0.5;
    right[1] = 2; // clipped to full scale

    const bytes = encodeWavBytes(buffer);
    const view = new DataView(bytes.buffer);
    expect(text(bytes, 0, 4)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(36 + 480 * 4);
    expect(text(bytes, 8, 12)).toBe("WAVE");
    expect(text(bytes, 12, 16)).toBe("fmt ");
    expect(view.getUint32(16, true)).toBe(16);
    expect(view.getUint16(20, true)).toBe(1); // PCM
    expect(view.getUint16(22, true)).toBe(2); // channels
    expect(view.getUint32(24, true)).toBe(48000);
    expect(view.getUint32(28, true)).toBe(48000 * 4); // byte rate
    expect(view.getUint16(32, true)).toBe(4); // block align
    expect(view.getUint16(34, true)).toBe(16);
    expect(text(bytes, 36, 40)).toBe("data");
    expect(view.getUint32(40, true)).toBe(480 * 2 * 2);
    expect(bytes.length).toBe(44 + 480 * 2 * 2);
    // sample count = frames x channels
    expect((bytes.length - 44) / 2).toBe(960);

    expect(view.getInt16(44, true)).toBe(32767); // L0
    expect(view.getInt16(46, true)).toBe(-32768); // R0
    expect(view.getInt16(48, true)).toBe(Math.round(0.5 * 32767)); // L1
    expect(view.getInt16(50, true)).toBe(32767); // R1, clipped
    expect(view.getInt16(52, true)).toBe(0);
  });

  it("returns a wav blob of the same size", () => {
    const blob = encodeWav(fakeBuffer(0.005, 44100, 1));
    expect(blob.type).toBe("audio/wav");
    expect(blob.size).toBe(44 + Math.round(0.005 * 44100) * 2);
  });
});
