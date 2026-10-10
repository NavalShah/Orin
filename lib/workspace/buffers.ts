// Decoded audio for the Workspace, by asset id. Decoding uses an OfflineAudioContext so it
// works before the first user gesture; the live AudioContext is only created on play.
import { computePeaks, type WavePeaks } from "../audio/peaks";

export const AUDIO_EXTENSIONS = ["wav", "mp3", "m4a", "flac", "ogg"] as const;
export const AUDIO_ACCEPT = AUDIO_EXTENSIONS.map((e) => `.${e}`).join(",") + ",audio/*";

const extension = (name: string) => (name.includes(".") ? name.split(".").pop()!.toLowerCase() : "");

/** wav, mp3, m4a, flac or ogg, judged by the file name (browsers report audio types inconsistently). */
export function isSupportedAudioFile(file: { name: string }): boolean {
  return (AUDIO_EXTENSIONS as readonly string[]).includes(extension(file.name));
}

/** Why a file was refused, in words for the user. Null when it looks fine. */
export function fileProblem(file: { name: string; size: number }): string | null {
  if (!isSupportedAudioFile(file)) {
    const ext = extension(file.name);
    return `${file.name}: ${ext ? `.${ext} files are` : "this kind of file is"} not supported. Use wav, mp3, m4a, flac or ogg.`;
  }
  if (file.size === 0) return `${file.name} is empty.`;
  return null;
}

export class DecodeError extends Error {
  constructor(name: string) {
    super(`${name} could not be decoded. The file may be damaged, or in a format this browser cannot play.`);
    this.name = "DecodeError";
  }
}

/** Decode a whole audio file. Throws DecodeError with a message that can be shown. */
export async function decodeAudio(bytes: ArrayBuffer, name = "This file"): Promise<AudioBuffer> {
  const Ctor =
    window.OfflineAudioContext ??
    (window as unknown as { webkitOfflineAudioContext?: typeof OfflineAudioContext }).webkitOfflineAudioContext;
  if (!Ctor) throw new DecodeError(name);
  try {
    // decodeAudioData takes ownership of the bytes, so hand it a copy.
    return await new Ctor(2, 1, 48000).decodeAudioData(bytes.slice(0));
  } catch {
    throw new DecodeError(name);
  }
}

export class BufferCache {
  private buffers = new Map<string, AudioBuffer>();
  private peaks = new Map<string, WavePeaks>();
  private pending = new Map<string, Promise<AudioBuffer>>();
  private errors = new Map<string, string>();
  private listeners = new Set<() => void>();
  private version = 0;

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };
  /** Changes whenever a buffer arrives or fails, for useSyncExternalStore. */
  getVersion = () => this.version;
  private changed() {
    this.version += 1;
    this.listeners.forEach((fn) => fn());
  }

  get = (assetId: string) => this.buffers.get(assetId);
  has = (assetId: string) => this.buffers.has(assetId);
  peaksOf = (assetId: string) => this.peaks.get(assetId);
  errorOf = (assetId: string) => this.errors.get(assetId);
  isLoading = (assetId: string) => this.pending.has(assetId);
  /** Every decoded buffer, for an offline render. */
  all = (): ReadonlyMap<string, AudioBuffer> => this.buffers;

  put(assetId: string, buffer: AudioBuffer): void {
    this.buffers.set(assetId, buffer);
    this.peaks.set(assetId, computePeaks(buffer));
    this.errors.delete(assetId);
    this.changed();
  }

  /** Fetch and decode once per asset; later calls share the result. */
  load(assetId: string, fetchBytes: () => Promise<ArrayBuffer>, name?: string): Promise<AudioBuffer> {
    const ready = this.buffers.get(assetId);
    if (ready) return Promise.resolve(ready);
    const running = this.pending.get(assetId);
    if (running) return running;
    const task = (async () => {
      try {
        const buffer = await decodeAudio(await fetchBytes(), name);
        this.pending.delete(assetId);
        this.put(assetId, buffer);
        return buffer;
      } catch (err) {
        this.pending.delete(assetId);
        this.errors.set(assetId, err instanceof Error ? err.message : "The audio could not be loaded.");
        this.changed();
        throw err;
      }
    })();
    this.pending.set(assetId, task);
    return task;
  }
}
