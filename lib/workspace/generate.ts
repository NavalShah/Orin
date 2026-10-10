// "Generate here" (spec 12.5): the same Gemini -> ElevenLabs flow as Chat, from a prompt
// box in the Workspace. A new chat thread carries the request, so it also shows up in Chat.
import { api, ApiError, type Asset, type MusicSpec } from "../api";

export type GenerateResult =
  | { kind: "asset"; asset: Asset }
  /** Orin answered in words instead of proposing a track (a question, or a request it won't make). */
  | { kind: "reply"; text: string };

export async function generateHere(
  prompt: string,
  onStatus: (text: string) => void,
  signal?: AbortSignal,
): Promise<GenerateResult> {
  onStatus("Asking Orin what to make");
  const thread = await api.createThread(`Workspace: ${prompt.slice(0, 60)}`);
  let spec: MusicSpec | null = null;
  let reply = "";
  for await (const event of api.sendMessage(thread.id, { text: prompt }, signal)) {
    if (event.type === "error") throw new ApiError(event.error ?? { message: "Orin could not answer." });
    if (event.type === "delta" && event.text) reply += event.text;
    if (event.type === "card" && event.card?.kind === "spec" && event.card.spec) spec = event.card.spec;
    if (event.type === "message" && event.message) {
      const card = event.message.cards?.find((c) => c.kind === "spec" && c.spec);
      if (card?.spec) spec = card.spec;
      if (!reply) reply = event.message.text;
    }
  }
  if (!spec) {
    return { kind: "reply", text: reply.trim() || "Orin did not propose a track. Describe the music you want, for example its style, mood and length." };
  }

  onStatus("Generating the track");
  const ref = await api.generate(spec, thread.id);
  const job = await api.watchJob(
    ref.job_id,
    (j) => onStatus(`${j.message ?? j.stage ?? "Generating the track"} (${Math.round((j.progress ?? 0) * 100)}%)`),
    signal,
  );
  if (job.status !== "done" || !job.result) {
    throw new ApiError(job.error ?? { message: job.status === "cancelled" ? "Generation was cancelled." : "Generation did not finish." });
  }
  if (job.result.kind === "asset") return { kind: "asset", asset: await api.getAsset(job.result.id) };
  if (job.result.kind === "message") {
    const messages = await api.messages(thread.id);
    const message = messages.find((m) => m.id === job.result!.id) ?? messages[messages.length - 1];
    const asset = message?.cards?.find((c) => c.kind === "audio" && c.asset)?.asset;
    if (asset) return { kind: "asset", asset };
  }
  throw new ApiError({ message: "The track was generated but could not be found in the Library." });
}
