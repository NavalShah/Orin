// Typed client for the Orin API. The contract is server/schemas.py; the types in
// ./api-types.ts are generated from it (npm run gen:api). Keys never reach the browser:
// this file only ever talks to the Orin server.
import type { components } from "./api-types";

type S = components["schemas"];

export type Asset = S["Asset"];
export type Card = S["Card"];
export type ChatEvent = S["ChatEvent"];
export type ConversionOut = S["ConversionOut"];
export type ConversionSummary = S["ConversionSummary"];
export type ConvertOptionsIn = S["ConvertOptionsIn"];
export type ErrorBody = S["ErrorBody"];
export type Explanation = S["Explanation"];
export type FormatCandidate = S["FormatCandidate"];
export type FormatInfo = S["FormatInfo"];
export type FormatScore = S["FormatScore"];
export type FormatsResponse = S["FormatsResponse"];
export type GlossaryEntry = S["GlossaryEntry"];
export type Health = S["Health"];
export type IRSummary = S["IRSummary"];
export type Job = S["Job"];
export type JobRef = S["JobRef"];
export type LossItem = S["LossItem"];
export type LossReport = S["LossReport"];
export type Message = S["Message"];
export type ModeCandidate = S["ModeCandidate"];
export type ModeInfo = S["ModeInfo"];
export type MusicSpec = S["MusicSpec"];
export type Narration = S["Narration"];
export type Notice = S["Notice"];
export type OutputFile = S["OutputFile"];
export type ParseProblem = S["ParseProblem"];
export type Peaks = S["Peaks"];
export type ProjectOut = S["ProjectOut"];
export type ProjectSummary = S["ProjectSummary"];
export type ScoreNote = S["ScoreNote"];
export type Session = S["Session"];
export type TemplateInfo = S["TemplateInfo"];
export type Thread = S["Thread"];
export type User = S["User"];
export type UserPrefs = S["UserPrefs"];
export type Level = UserPrefs["level"];

// Request bodies: only the fields you want to set.
export type ConvertRequest = {
  asset_id?: string;
  notes_text?: string;
  notes_syntax?: S["ConvertRequest"]["notes_syntax"];
  input_format?: string;
  output_format: string;
  options?: Partial<ConvertOptionsIn>;
};
export type RerunRequest = Partial<S["RerunRequest"]>;
export type MessageCreate = {
  text: string;
  attachment_asset_ids?: string[];
  genre_id?: string;
  context?: { conversion_id?: string; item_indexes?: number[] };
};

export const API_ORIGIN = (process.env.NEXT_PUBLIC_ORIN_API ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const BASE = `${API_ORIGIN}/api/v1`;

export class ApiError extends Error {
  code: string;
  hint: string | null;
  retryable: boolean;
  status: number;
  constructor(body: Partial<ErrorBody> & { message: string }, status = 0) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code ?? "error";
    this.hint = body.hint ?? null;
    this.retryable = body.retryable ?? false;
    this.status = status;
  }
}

/** A message safe to show for any thrown value. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.hint ? `${err.message} ${err.hint}` : err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

// --- session ---------------------------------------------------------------

const TOKEN_KEY = "orin.token";
const listeners = new Set<() => void>();

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage unavailable: the session lasts until reload
  }
  listeners.forEach((fn) => fn());
}

/** Called whenever the user signs in, signs out, or the server rejects the token. */
export function onAuthChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// --- transport -------------------------------------------------------------

type Init = Omit<RequestInit, "body"> & { json?: unknown; body?: BodyInit | null };

function headers(init: Init): Headers {
  const h = new Headers(init.headers);
  const token = getToken();
  if (token) h.set("Authorization", `Bearer ${token}`);
  if (init.json !== undefined) h.set("Content-Type", "application/json");
  return h;
}

async function toError(res: Response): Promise<ApiError> {
  let body: Partial<ErrorBody> & { message: string } = {
    code: `http_${res.status}`,
    message: `The server answered ${res.status}.`,
    retryable: res.status >= 500,
  };
  try {
    const data = await res.json();
    if (data?.error?.message) body = data.error;
  } catch {
    // not JSON: keep the generic message
  }
  if (res.status === 401) setToken(null);
  return new ApiError(body, res.status);
}

async function send(path: string, init: Init = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: headers(init),
      body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
    });
  } catch (err) {
    if ((err as Error)?.name === "AbortError") throw err;
    throw new ApiError({
      code: "network",
      message: `Can't reach the Orin server at ${API_ORIGIN}.`,
      hint: "Check that it is running, then try again.",
      retryable: true,
    });
  }
  if (!res.ok) throw await toError(res);
  return res;
}

async function request<T>(path: string, init: Init = {}): Promise<T> {
  const res = await send(path, init);
  return (await res.json()) as T;
}

/** Server-sent events over fetch, so the Authorization header can be sent. */
async function* stream<T>(path: string, init: Init = {}): AsyncGenerator<T> {
  const res = await send(path, { ...init, headers: { ...(init.headers as object), Accept: "text/event-stream" } });
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const data = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) yield JSON.parse(data) as T;
      }
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

const TERMINAL = new Set<Job["status"]>(["done", "failed", "cancelled", "awaiting_input"]);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const query = (q: Record<string, string | number | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

// --- endpoints -------------------------------------------------------------

export const api = {
  health: () => request<Health>("/health"),

  async signIn(name: string): Promise<Session> {
    const session = await request<Session>("/auth/session", { method: "POST", json: { name } });
    setToken(session.token);
    return session;
  },
  me: () => request<User>("/auth/me"),
  updatePrefs: (prefs: Partial<UserPrefs>) => request<User>("/auth/me", { method: "PATCH", json: prefs }),
  async signOut(): Promise<void> {
    try {
      await request("/auth/logout", { method: "POST" });
    } finally {
      setToken(null);
    }
  },

  formats: () => request<FormatsResponse>("/formats"),
  glossary: () => request<GlossaryEntry[]>("/glossary"),

  uploadAsset(file: Blob, opts: { filename?: string; source?: Asset["source"] } = {}): Promise<Asset> {
    const form = new FormData();
    form.append("file", file, opts.filename ?? (file as File).name ?? "upload");
    form.append("source", opts.source ?? "upload");
    return request<Asset>("/assets", { method: "POST", body: form });
  },
  listAssets: (q: { kind?: Asset["kind"]; source?: Asset["source"] } = {}) => request<Asset[]>(`/assets${query(q)}`),
  getAsset: (id: string) => request<Asset>(`/assets/${id}`),
  deleteAsset: (id: string) => request<{ ok: boolean }>(`/assets/${id}`, { method: "DELETE" }),
  peaks: (id: string, buckets = 800) => request<Peaks>(`/assets/${id}/peaks${query({ buckets })}`),
  /** Absolute, signed URL for <audio src> and download links. */
  assetUrl: (asset: Pick<Asset, "url">) => `${API_ORIGIN}${asset.url}`,
  async assetBytes(asset: Pick<Asset, "url">): Promise<ArrayBuffer> {
    const res = await fetch(`${API_ORIGIN}${asset.url}`);
    if (!res.ok) throw await toError(res);
    return res.arrayBuffer();
  },

  convert: (req: ConvertRequest) => request<JobRef>("/convert", { method: "POST", json: req }),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  /** Follow a job to a terminal status. Streams progress, and falls back to polling. */
  async watchJob(id: string, onUpdate?: (job: Job) => void, signal?: AbortSignal): Promise<Job> {
    let last: Job | null = null;
    try {
      for await (const job of stream<Job>(`/jobs/${id}/events`, { signal })) {
        last = job;
        onUpdate?.(job);
        if (TERMINAL.has(job.status)) return job;
      }
    } catch (err) {
      if ((err as Error)?.name === "AbortError") throw err;
    }
    while (!last || !TERMINAL.has(last.status)) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      last = await api.job(id);
      onUpdate?.(last);
      if (!TERMINAL.has(last.status)) await sleep(1000);
    }
    return last;
  },

  conversions: () => request<ConversionSummary[]>("/conversions"),
  conversion: (id: string) => request<ConversionOut>(`/conversions/${id}`),
  async conversionIr(id: string): Promise<Blob> {
    return (await send(`/conversions/${id}/ir`)).blob();
  },
  rerun: (id: string, req: RerunRequest) => request<JobRef>(`/conversions/${id}/rerun`, { method: "POST", json: req }),

  explain: (req: { conversion_id: string; item_indexes?: number[]; level?: Level; question?: string }) =>
    request<Explanation>("/explain", { method: "POST", json: req }),
  narrate: (text: string) => request<Narration>("/narrate", { method: "POST", json: { text } }),

  threads: () => request<Thread[]>("/chat/threads"),
  createThread: (title?: string) => request<Thread>("/chat/threads", { method: "POST", json: { title } }),
  renameThread: (id: string, title: string) => request<Thread>(`/chat/threads/${id}`, { method: "PATCH", json: { title } }),
  deleteThread: (id: string) => request<{ ok: boolean }>(`/chat/threads/${id}`, { method: "DELETE" }),
  messages: (threadId: string) => request<Message[]>(`/chat/threads/${threadId}/messages`),
  sendMessage: (threadId: string, body: MessageCreate, signal?: AbortSignal) =>
    stream<ChatEvent>(`/chat/threads/${threadId}/messages`, { method: "POST", json: body, signal }),
  generate: (spec: MusicSpec, threadId?: string) =>
    request<JobRef>("/generate", { method: "POST", json: { spec, thread_id: threadId } }),

  projects: () => request<ProjectSummary[]>("/projects"),
  createProject: (name: string, project: unknown, version = 1) =>
    request<ProjectOut>("/projects", { method: "POST", json: { name, project, version } }),
  getProject: (id: string) => request<ProjectOut>(`/projects/${id}`),
  saveProject: (id: string, name: string, project: unknown, version = 1) =>
    request<ProjectOut>(`/projects/${id}`, { method: "PUT", json: { name, project, version } }),
  duplicateProject: (id: string) => request<ProjectOut>(`/projects/${id}/duplicate`, { method: "POST" }),
  deleteProject: (id: string) => request<{ ok: boolean }>(`/projects/${id}`, { method: "DELETE" }),
};
