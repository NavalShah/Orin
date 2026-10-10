// Min/max peaks for canvas waveforms (spec 12.6, 12.9): computed once per decoded buffer,
// then any zoom level is drawn from them.

export type WavePeaks = {
  /** Lowest and highest sample in each bucket, over all channels. */
  min: Float32Array;
  max: Float32Array;
  buckets: number;
  durationSec: number;
};

type BufferLike = Pick<AudioBuffer, "numberOfChannels" | "length" | "duration" | "getChannelData">;

/** About 100 buckets per second, capped so a long file stays cheap. */
export const defaultBuckets = (durationSec: number) => Math.max(1, Math.min(8000, Math.ceil(durationSec * 100)));

export function computePeaks(buffer: BufferLike, buckets = defaultBuckets(buffer.duration)): WavePeaks {
  const n = Math.max(1, Math.min(Math.floor(buckets), Math.max(1, buffer.length)));
  const min = new Float32Array(n);
  const max = new Float32Array(n);
  const per = buffer.length / n;
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let b = 0; b < n; b++) {
      const from = Math.floor(b * per);
      const to = Math.min(data.length, Math.max(from + 1, Math.floor((b + 1) * per)));
      let lo = c === 0 ? Infinity : min[b];
      let hi = c === 0 ? -Infinity : max[b];
      for (let i = from; i < to; i++) {
        const v = data[i];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      min[b] = Number.isFinite(lo) ? lo : 0;
      max[b] = Number.isFinite(hi) ? hi : 0;
    }
  }
  return { min, max, buckets: n, durationSec: buffer.duration };
}

/** The min and max over a stretch of the file, for one pixel column. */
export function peakRange(peaks: WavePeaks, fromSec: number, toSec: number): [number, number] {
  if (peaks.durationSec <= 0) return [0, 0];
  const scale = peaks.buckets / peaks.durationSec;
  const from = Math.max(0, Math.min(peaks.buckets - 1, Math.floor(fromSec * scale)));
  const to = Math.max(from + 1, Math.min(peaks.buckets, Math.ceil(toSec * scale)));
  let lo = Infinity;
  let hi = -Infinity;
  for (let b = from; b < to; b++) {
    if (peaks.min[b] < lo) lo = peaks.min[b];
    if (peaks.max[b] > hi) hi = peaks.max[b];
  }
  return [Number.isFinite(lo) ? lo : 0, Number.isFinite(hi) ? hi : 0];
}

/** Draw a stretch of the waveform into a canvas area, one column per pixel. */
export function drawPeaks(
  g: CanvasRenderingContext2D,
  peaks: WavePeaks,
  area: { x: number; y: number; width: number; height: number },
  fromSec: number,
  toSec: number,
): void {
  const width = Math.max(1, Math.floor(area.width));
  const mid = area.y + area.height / 2;
  const half = area.height / 2;
  const perPx = (toSec - fromSec) / width;
  for (let x = 0; x < width; x++) {
    const t = fromSec + x * perPx;
    if (t >= peaks.durationSec) break;
    const [lo, hi] = peakRange(peaks, t, t + perPx);
    const top = mid - Math.min(1, Math.max(-1, hi)) * half;
    const bottom = mid - Math.min(1, Math.max(-1, lo)) * half;
    g.fillRect(area.x + x, top, 1, Math.max(1, bottom - top));
  }
}
