// WAV export (spec 12.8): 16-bit PCM, channels interleaved.

type BufferLike = Pick<AudioBuffer, "numberOfChannels" | "sampleRate" | "length" | "getChannelData">;

export function encodeWavBytes(buffer: BufferLike): Uint8Array<ArrayBuffer> {
  const channels = Math.max(1, buffer.numberOfChannels);
  const frames = buffer.length;
  const blockAlign = channels * 2;
  const dataBytes = frames * blockAlign;
  const out = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(out);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };

  ascii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * blockAlign, true); // byte rate
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, dataBytes, true);

  const data: Float32Array[] = [];
  for (let c = 0; c < channels; c++) data.push(buffer.getChannelData(c));
  let offset = 44;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      const s = Math.max(-1, Math.min(1, data[c][i] || 0));
      view.setInt16(offset, Math.round(s < 0 ? s * 0x8000 : s * 0x7fff), true);
      offset += 2;
    }
  }
  return new Uint8Array(out);
}

export function encodeWav(buffer: BufferLike): Blob {
  return new Blob([encodeWavBytes(buffer)], { type: "audio/wav" });
}
