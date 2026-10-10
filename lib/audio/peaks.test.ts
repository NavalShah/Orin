import { describe, expect, it } from "vitest";
import { fakeBuffer } from "../workspace/testing/fakeAudioContext";
import { computePeaks, peakRange } from "./peaks";

describe("peaks", () => {
  it("finds the min and max of each bucket across channels", () => {
    const buffer = fakeBuffer(1, 1000, 2); // 1000 frames
    buffer.getChannelData(0)[10] = 0.8;
    buffer.getChannelData(1)[20] = -0.6;
    buffer.getChannelData(0)[950] = 0.3;
    const peaks = computePeaks(buffer, 10);
    expect(peaks.buckets).toBe(10);
    expect(peaks.max[0]).toBeCloseTo(0.8);
    expect(peaks.min[0]).toBeCloseTo(-0.6);
    expect(peaks.max[5]).toBe(0);
    expect(peaks.max[9]).toBeCloseTo(0.3);
    expect(peaks.durationSec).toBe(1);
  });

  it("reads a time range back", () => {
    const buffer = fakeBuffer(2, 1000, 1);
    buffer.getChannelData(0)[1500] = 0.9;
    const peaks = computePeaks(buffer, 20);
    expect(peakRange(peaks, 0, 1)[1]).toBe(0);
    expect(peakRange(peaks, 1.4, 1.6)[1]).toBeCloseTo(0.9);
    expect(peakRange(peaks, 5, 6)).toEqual([0, 0]);
  });

  it("never asks for more buckets than there are samples", () => {
    expect(computePeaks(fakeBuffer(0.001, 1000, 1), 50).buckets).toBe(1);
  });
});
