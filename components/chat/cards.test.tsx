import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { api, type Asset, type Card, type Job, type MusicSpec } from "../../lib/api";
import { makeRegistryFixture } from "../studio/fixtures";
import { AudioCard } from "./AudioCard";
import { ConversionCard } from "./ConversionCard";
import { SpecCard } from "./SpecCard";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, generate: vi.fn(), watchJob: vi.fn(), peaks: vi.fn(), assetBytes: vi.fn() },
  };
});

const job = (over: Partial<Job>): Job => ({
  id: "j1", type: "generate", status: "running", progress: 0, stage: null, message: null, error: null, result: null,
  created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-10T10:00:00Z", ...over,
});

const spec: MusicSpec = {
  genre_id: "fusion",
  sections: [
    { genre_id: "fmt_swara", description: "slow opening, voice and drone", duration_s: 30 },
    { genre_id: "jazz", description: "piano takes the theme", duration_s: 30 },
  ],
  mood: ["calm", "devotional"],
  tempo_bpm: 70,
  key_or_tonic: "D",
  mode_hint: "Mode One",
  instruments: ["voice", "piano"],
  vocals: "wordless",
  duration_s: 60,
  structure_notes: "No drums until the second half.",
  avoid: ["heavy distortion"],
  assumptions: ["Assumed wordless singing since no lyrics were given."],
  learning_goal: "Hear the same theme in two styles.",
};

const card = (over: Partial<Card>): Card => ({
  kind: "spec", text: null, spec: null, notes: [], asset: null, standin: false, standin_reason: null, provider: null,
  conversion_id: null, job_id: null, ...over,
});

const asset: Asset = {
  id: "a1", kind: "audio", source: "generated", filename: "calm-piece.wav", media_type: "audio/wav", size_bytes: 2048,
  sha256: "x", duration_s: 30, meta: {}, created_at: "2026-10-10T10:00:00Z", url: "/api/v1/assets/a1/file?sig=1", standin: false,
};

beforeAll(() => {
  // jsdom has no canvas; the waveform simply does not draw.
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
});

beforeEach(() => {
  vi.mocked(api.generate).mockReset();
  vi.mocked(api.watchJob).mockReset();
  vi.mocked(api.peaks).mockReset().mockResolvedValue({ duration_s: 30, buckets: 4, peaks: [0.1, 0.6, 0.3, 0.2] });
});

describe("spec card", () => {
  const specCard = card({ kind: "spec", spec, notes: ["An artist's name was replaced with a description of the style."] });

  it("shows the plan: chips, sections, assumptions and notes", () => {
    render(<SpecCard card={specCard} threadId="t1" registry={makeRegistryFixture()} />);
    expect(screen.getByRole("heading", { name: "Here's what I'll make" })).toBeInTheDocument();

    const chips = within(screen.getByRole("list", { name: "The plan" })).getAllByRole("listitem").map((li) => li.textContent);
    expect(chips).toEqual([
      "Genre fusion",
      "Tempo 70 BPM",
      "Length 1:00",
      "Mood calm, devotional",
      "Instruments voice, piano",
      "Vocals Wordless vocals",
      "Key or tonic D",
      "Mode hint Mode One (a hint, not a guarantee)",
    ]);

    const sections = within(screen.getByText("Sections").parentElement as HTMLElement).getAllByRole("listitem");
    expect(sections).toHaveLength(2);
    // A registry id shows its display name; a plain genre word is shown as written.
    expect(sections[0]).toHaveTextContent("Swara Notation: slow opening, voice and drone (0:30)");
    expect(sections[1]).toHaveTextContent("jazz: piano takes the theme (0:30)");

    const notes = within(screen.getByText("Assumptions and notes").parentElement as HTMLElement).getAllByRole("listitem");
    expect(notes.map((n) => n.textContent)).toEqual([
      "Assumed wordless singing since no lyrics were given.",
      "An artist's name was replaced with a description of the style.",
    ]);
  });

  it("generates the confirmed spec for the thread and reports when the track is ready", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    vi.mocked(api.generate).mockResolvedValue({ job_id: "j1", conversion_id: null });
    (api.watchJob as Mock).mockImplementation(async (_id: string, onUpdate?: (j: Job) => void) => {
      onUpdate?.(job({ progress: 0.5, stage: "generate" }));
      return job({ status: "done", progress: 1, result: { kind: "message", id: "m9" } });
    });
    render(<SpecCard card={specCard} threadId="t1" registry={makeRegistryFixture()} onDone={onDone} />);

    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(api.generate).toHaveBeenCalledWith(spec, "t1");
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(api.watchJob).toHaveBeenCalledWith("j1", expect.any(Function), expect.any(AbortSignal));
    expect(screen.getByRole("status")).toHaveTextContent("The track is ready.");
  });

  it("sends the edited spec", async () => {
    const user = userEvent.setup();
    vi.mocked(api.generate).mockResolvedValue({ job_id: "j1", conversion_id: null });
    vi.mocked(api.watchJob).mockResolvedValue(job({ status: "done", progress: 1 }));
    render(<SpecCard card={specCard} threadId="t1" registry={makeRegistryFixture()} />);

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const tempo = screen.getByLabelText(/Tempo/);
    await user.clear(tempo);
    await user.type(tempo, "96");
    await user.selectOptions(screen.getByLabelText("Genre"), "Staff Notation");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await user.click(screen.getByRole("button", { name: "Generate" }));

    const sent = vi.mocked(api.generate).mock.calls[0][0];
    expect(sent.tempo_bpm).toBe(96);
    expect(sent.genre_id).toBe("fmt_staff");
    expect(sent.assumptions).toEqual(spec.assumptions);
  });

  it("shows a failure with a way to try again", async () => {
    const user = userEvent.setup();
    vi.mocked(api.generate).mockResolvedValue({ job_id: "j1", conversion_id: null });
    vi.mocked(api.watchJob).mockResolvedValue(
      job({ status: "failed", error: { code: "quota", message: "The daily limit was reached.", hint: "Try again tomorrow.", retryable: false } }),
    );
    render(<SpecCard card={specCard} registry={makeRegistryFixture()} />);
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The daily limit was reached. Try again tomorrow.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("audio card", () => {
  it("labels a stand-in in words, with the reason", async () => {
    render(<AudioCard card={card({ kind: "audio", asset: { ...asset, standin: true }, standin: true, standin_reason: "No music key is set." })} />);
    await waitFor(() => expect(api.peaks).toHaveBeenCalledWith("a1", 600));
    expect(screen.getByText("Offline stand-in")).toBeInTheDocument();
    expect(screen.getByText(/not a\s+track from the music generator\. Why: No music key is set\./)).toBeInTheDocument();
  });

  it("does not call a real track a stand-in", async () => {
    render(
      <AudioCard card={card({ kind: "audio", asset, provider: { name: "elevenlabs", model: "music-1", request_id: null, duration_s: 30 } })} />,
    );
    await waitFor(() => expect(api.peaks).toHaveBeenCalled());
    expect(screen.queryByText("Offline stand-in")).toBeNull();
    expect(screen.getByText("Made with elevenlabs (music-1).")).toBeInTheDocument();
  });

  it("offers play, download and the three handoffs", async () => {
    const user = userEvent.setup();
    const onExplain = vi.fn();
    render(<AudioCard card={card({ kind: "audio", asset })} onExplain={onExplain} />);
    await waitFor(() => expect(api.peaks).toHaveBeenCalled());
    expect(screen.getByRole("img", { name: /Waveform of calm-piece\.wav/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play calm-piece.wav" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download calm-piece.wav" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in Workspace" })).toHaveAttribute("href", "/workspace?import_asset=a1");
    expect(screen.getByRole("link", { name: "Transcribe" })).toHaveAttribute("href", "/studio?asset=a1");
    await user.click(screen.getByRole("button", { name: "Explain" }));
    expect(onExplain).toHaveBeenCalledWith(asset);
  });
});

describe("conversion card", () => {
  it("shows progress while the job runs, then links to the Notation Studio", async () => {
    let finish: (j: Job) => void = () => undefined;
    (api.watchJob as Mock).mockImplementation((_id: string, onUpdate?: (j: Job) => void) => {
      onUpdate?.(job({ type: "convert", stage: "tonic", progress: 0.4 }));
      return new Promise<Job>((resolve) => {
        finish = resolve;
      });
    });
    render(<ConversionCard card={card({ kind: "conversion", job_id: "j7", conversion_id: "c7" })} />);
    expect(await screen.findByText("Finding the tonic...")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Transcription progress" })).toHaveAttribute("value", "0.4");
    expect(screen.queryByRole("link")).toBeNull();

    finish(job({ type: "convert", status: "done", progress: 1, result: { kind: "conversion", id: "c7" } }));
    expect(await screen.findByRole("link", { name: "Open in the Notation Studio" })).toHaveAttribute("href", "/studio?conversion=c7");
    expect(screen.getByRole("status")).toHaveTextContent("The notation is ready.");
  });
});
