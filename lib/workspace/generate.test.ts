import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createThread: vi.fn(),
  sendMessage: vi.fn(),
  generate: vi.fn(),
  watchJob: vi.fn(),
  messages: vi.fn(),
  getAsset: vi.fn(),
}));

vi.mock("../api", () => {
  class ApiError extends Error {
    constructor(body: { message: string }) {
      super(body.message);
    }
  }
  return { api: mocks, ApiError };
});

import { generateHere } from "./generate";

async function* events(list: unknown[]) {
  for (const e of list) yield e;
}

const spec = { genre_id: "carnatic", duration_s: 20 };
const asset = { id: "gen1", kind: "audio", filename: "tabla.mp3" };

describe("generateHere", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockReset());
    mocks.createThread.mockResolvedValue({ id: "th1" });
    mocks.generate.mockResolvedValue({ job_id: "job1" });
  });

  it("takes the spec card from the chat stream, generates, and returns the new asset", async () => {
    mocks.sendMessage.mockReturnValue(
      events([{ type: "intent" }, { type: "card", card: { kind: "spec", spec } }, { type: "message", message: { id: "m1", text: "Here's what I'll make", cards: [] } }, { type: "done" }]),
    );
    mocks.watchJob.mockImplementation(async (_id: string, onUpdate: (job: unknown) => void) => {
      onUpdate({ status: "running", progress: 0.5, stage: "generate", message: null });
      return { status: "done", result: { kind: "message", id: "m2" } };
    });
    mocks.messages.mockResolvedValue([
      { id: "m1", cards: [{ kind: "spec", spec }] },
      { id: "m2", cards: [{ kind: "audio", asset }] },
    ]);
    const status: string[] = [];
    const result = await generateHere("a slow tabla groove", (s) => status.push(s));
    expect(result).toEqual({ kind: "asset", asset });
    expect(mocks.sendMessage).toHaveBeenCalledWith("th1", { text: "a slow tabla groove" }, undefined);
    expect(mocks.generate).toHaveBeenCalledWith(spec, "th1");
    expect(status.at(-1)).toBe("generate (50%)");
  });

  it("accepts a job that results in an asset directly", async () => {
    mocks.sendMessage.mockReturnValue(events([{ type: "card", card: { kind: "spec", spec } }]));
    mocks.watchJob.mockResolvedValue({ status: "done", result: { kind: "asset", id: "gen1" } });
    mocks.getAsset.mockResolvedValue(asset);
    expect(await generateHere("x", () => undefined)).toEqual({ kind: "asset", asset });
  });

  it("returns Orin's words when no track was proposed", async () => {
    mocks.sendMessage.mockReturnValue(events([{ type: "delta", text: "Which raga " }, { type: "delta", text: "do you mean?" }, { type: "done" }]));
    expect(await generateHere("hmm", () => undefined)).toEqual({ kind: "reply", text: "Which raga do you mean?" });
    expect(mocks.generate).not.toHaveBeenCalled();
  });

  it("throws a showable error when the stream or the job fails", async () => {
    mocks.sendMessage.mockReturnValue(events([{ type: "error", error: { message: "Daily limit reached." } }]));
    await expect(generateHere("x", () => undefined)).rejects.toThrow("Daily limit reached.");

    mocks.sendMessage.mockReturnValue(events([{ type: "card", card: { kind: "spec", spec } }]));
    mocks.watchJob.mockResolvedValue({ status: "failed", error: { message: "The music service is busy." }, result: null });
    await expect(generateHere("x", () => undefined)).rejects.toThrow("The music service is busy.");
  });
});
