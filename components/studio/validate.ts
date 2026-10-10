// Checks made the moment a file is chosen (spec 11.3 step 2): type by extension and MIME,
// size against the server's limit, and for audio the length. The server checks again.
import type { FormatsResponse } from "@/lib/api";
import { bytes, clock } from "@/lib/format";

export const AUDIO_EXT = ["wav", "mp3", "flac", "ogg", "oga", "opus", "m4a", "aac", "aif", "aiff", "webm"];
export const NOTES_EXT = ["txt", "sargam", "abc", "musicxml", "xml", "mxl", "mid", "midi"];
export const ACCEPT = [...AUDIO_EXT, ...NOTES_EXT].map((e) => `.${e}`).join(",");

const AUDIO_MIME_EXTRA = ["video/webm", "video/mp4", "video/ogg", "application/ogg", "application/octet-stream"];
const NOTES_MIME = [
  "application/xml", "application/vnd.recordare.musicxml+xml", "application/vnd.recordare.musicxml",
  "audio/midi", "audio/mid", "audio/x-midi", "application/x-midi", "application/octet-stream", "application/zip",
];

export type FileKind = "audio" | "notes";
export type Limits = FormatsResponse["limits"];

export function validateFile(
  file: { name: string; type: string; size: number },
  limits: Limits,
): { kind: FileKind | null; problems: string[] } {
  const problems: string[] = [];
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  const mime = (file.type || "").toLowerCase().split(";")[0];
  let kind: FileKind | null = null;
  if (AUDIO_EXT.includes(ext)) kind = "audio";
  else if (NOTES_EXT.includes(ext)) kind = "notes";

  if (!kind) {
    problems.push(
      `${file.name} is not a type Orin can read. Use audio (${AUDIO_EXT.join(", ")}) or a notes file (${NOTES_EXT.join(", ")}).`,
    );
  } else if (kind === "audio" && mime && !mime.startsWith("audio/") && !AUDIO_MIME_EXTRA.includes(mime)) {
    problems.push(`${file.name} is named like audio, but the browser reports it as ${mime}.`);
  } else if (kind === "notes" && mime && !mime.startsWith("text/") && !NOTES_MIME.includes(mime)) {
    problems.push(`${file.name} is named like a notes file, but the browser reports it as ${mime}.`);
  }

  if (file.size === 0) problems.push(`${file.name} is empty.`);
  if (file.size > limits.max_upload_mb * 1024 * 1024) {
    problems.push(`${file.name} is ${bytes(file.size)}. The limit is ${limits.max_upload_mb} MB.`);
  }
  return { kind: problems.length ? null : kind, problems };
}

export function durationProblem(name: string, seconds: number | null | undefined, limits: Limits): string | null {
  if (seconds == null || !(seconds > limits.max_audio_seconds)) return null;
  return `${name} is ${clock(seconds)} long. The limit is ${clock(limits.max_audio_seconds)}.`;
}

/** The length of an audio file, read by an <audio> element. Null if this browser cannot tell. */
export function audioDuration(file: Blob, timeoutMs = 8000): Promise<number | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const audio = document.createElement("audio");
    let settled = false;
    const done = (value: number | null) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      resolve(value);
    };
    audio.preload = "metadata";
    audio.onloadedmetadata = () => done(Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : null);
    audio.onerror = () => done(null);
    setTimeout(() => done(null), timeoutMs);
    audio.src = url;
  });
}
