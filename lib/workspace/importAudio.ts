// "From my computer" (spec 12.3): check, decode, then upload so the audio persists in
// the Library. Every failure is an Error whose message can be shown as it is.
import { api, errorText, type Asset } from "../api";
import { decodeAudio, fileProblem, type BufferCache } from "./buffers";

export type ImportStage = "decoding" | "uploading";

export async function importAudioFile(
  file: File,
  cache: BufferCache,
  onStage?: (stage: ImportStage, buffer?: AudioBuffer) => void,
): Promise<{ asset: Asset; buffer: AudioBuffer }> {
  const problem = fileProblem(file);
  if (problem) throw new Error(problem);
  onStage?.("decoding");
  const buffer = await decodeAudio(await file.arrayBuffer(), file.name);
  onStage?.("uploading", buffer);
  let asset: Asset;
  try {
    asset = await api.uploadAsset(file, { source: "upload", filename: file.name });
  } catch (err) {
    throw new Error(`${file.name} could not be saved to the Library. ${errorText(err)}`);
  }
  cache.put(asset.id, buffer);
  return { asset, buffer };
}

export const stripExtension = (filename: string) => filename.replace(/\.[a-z0-9]{1,5}$/i, "") || filename;
