import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { api, type ChatEvent, type Message, type Thread } from "../../lib/api";
import { RegistryProvider } from "../shell/registry";
import { makeRegistryFixture } from "../studio/fixtures";
import { ChatScreen } from "./ChatScreen";

const nav = vi.hoisted(() => ({ search: "", router: { replace: vi.fn(), push: vi.fn() } }));

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.search),
  usePathname: () => "/chat",
}));
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
    api: {
      ...actual.api,
      threads: vi.fn(),
      createThread: vi.fn(),
      renameThread: vi.fn(),
      deleteThread: vi.fn(),
      messages: vi.fn(),
      sendMessage: vi.fn(),
      getAsset: vi.fn(),
      listAssets: vi.fn(),
      peaks: vi.fn(),
      generate: vi.fn(),
      watchJob: vi.fn(),
    },
  };
});

const thread = (over: Partial<Thread> = {}): Thread => ({
  id: "t1", title: "New chat", created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-10T10:00:00Z", summary: null, ...over,
});
const message = (over: Partial<Message>): Message => ({
  id: "m1", thread_id: "t1", role: "assistant", text: "", cards: [], track_ids: [], spec: null, created_at: "2026-10-10T10:00:00Z", meta: {}, ...over,
});
const event = (over: Partial<ChatEvent> & Pick<ChatEvent, "type">): ChatEvent => ({
  intent: null, text: null, card: null, job_id: null, message: null, error: null, ...over,
});

function renderChat() {
  return render(
    <RegistryProvider value={makeRegistryFixture()}>
      <ChatScreen />
    </RegistryProvider>,
  );
}

beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
});

beforeEach(() => {
  nav.search = "";
  nav.router.replace.mockReset();
  for (const fn of Object.values(api)) if (vi.isMockFunction(fn)) (fn as Mock).mockReset();
  vi.mocked(api.threads).mockResolvedValue([]);
  vi.mocked(api.listAssets).mockResolvedValue([]);
  vi.mocked(api.messages).mockResolvedValue([]);
  vi.mocked(api.createThread).mockResolvedValue(thread());
});

describe("Chat", () => {
  it("streams a reply into a polite live region, then shows the stored message", async () => {
    const user = userEvent.setup();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const stored = message({ id: "m2", text: "Hello there." });
    (api.sendMessage as Mock).mockImplementation(async function* () {
      yield event({ type: "intent", intent: "chat" });
      yield event({ type: "delta", text: "Hello " });
      await gate;
      yield event({ type: "delta", text: "there." });
      yield event({ type: "message", message: stored });
      yield event({ type: "done" });
    });
    vi.mocked(api.messages).mockResolvedValue([message({ id: "m1", role: "user", text: "Hi" }), stored]);
    renderChat();

    await user.type(screen.getByLabelText("Message Orin"), "Hi{Enter}");
    expect(api.createThread).toHaveBeenCalledTimes(1);
    expect(api.sendMessage).toHaveBeenCalledWith("t1", { text: "Hi" }, expect.any(AbortSignal));

    const partial = await screen.findByText("Hello");
    const live = partial.closest("[aria-live]") as HTMLElement;
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveAttribute("aria-busy", "true");
    // While the reply streams, nothing can be sent and Stop is offered.
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();

    release();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    expect(screen.getByText("Hello there.")).toBeInTheDocument();
    expect(screen.getByText("Hi")).toBeInTheDocument();
    expect(api.messages).toHaveBeenCalledWith("t1");
  });

  it("Shift+Enter makes a new line instead of sending", async () => {
    const user = userEvent.setup();
    renderChat();
    const box = screen.getByLabelText("Message Orin") as HTMLTextAreaElement;
    await user.type(box, "one{Shift>}{Enter}{/Shift}two");
    expect(box.value).toBe("one\ntwo");
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  it("builds genre chips from the registry and sends the chosen one", async () => {
    const user = userEvent.setup();
    (api.sendMessage as Mock).mockImplementation(async function* () {
      yield event({ type: "done" });
    });
    renderChat();
    const chips = within(screen.getByRole("group", { name: "Genre for this message (optional)" })).getAllByRole("button");
    expect(chips.map((c) => c.textContent)).toEqual(["Staff Notation", "Pop Lead Sheet", "Swara Notation"]);
    await user.click(chips[2]);
    expect(chips[2]).toHaveAttribute("aria-pressed", "true");
    await user.type(screen.getByLabelText("Message Orin"), "Make something calm");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(api.sendMessage).toHaveBeenCalledWith("t1", { text: "Make something calm", genre_id: "fmt_swara" }, expect.any(AbortSignal));
  });

  it("shows a stream error with Retry, and Retry sends again", async () => {
    const user = userEvent.setup();
    (api.sendMessage as Mock)
      .mockImplementationOnce(async function* () {
        yield event({ type: "error", error: { code: "llm", message: "Orin could not answer just now.", hint: "Try again in a moment.", retryable: true } });
      })
      .mockImplementationOnce(async function* () {
        yield event({ type: "delta", text: "Second try." });
        yield event({ type: "done" });
      });
    renderChat();
    await user.type(screen.getByLabelText("Message Orin"), "Hi{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Orin could not answer just now. Try again in a moment.");
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(2));
    expect((api.sendMessage as Mock).mock.calls[1].slice(0, 2)).toEqual(["t1", { text: "Hi" }]);
  });

  it("shows a thrown network error too", async () => {
    const user = userEvent.setup();
    (api.sendMessage as Mock).mockImplementation(async function* () {
      throw new Error("Can't reach the Orin server.");
    });
    renderChat();
    await user.type(screen.getByLabelText("Message Orin"), "Hi{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the Orin server.");
  });

  it("renders spec, audio and conversion cards from stored messages", async () => {
    vi.mocked(api.threads).mockResolvedValue([thread({ title: "Tracks" })]);
    vi.mocked(api.peaks).mockResolvedValue({ duration_s: 30, buckets: 2, peaks: [0.2, 0.4] });
    vi.mocked(api.messages).mockResolvedValue([
      message({
        id: "m1",
        text: "Here is a plan.",
        cards: [
          {
            kind: "spec", text: null, notes: [], asset: null, standin: false, standin_reason: null, provider: null, conversion_id: null, job_id: null,
            spec: {
              genre_id: "fmt_pop", sections: [], mood: [], tempo_bpm: null, key_or_tonic: null, mode_hint: null, instruments: [], vocals: "none",
              duration_s: 30, structure_notes: null, avoid: [], assumptions: ["Assumed 30 seconds."], learning_goal: null,
            },
          },
          {
            kind: "audio", text: null, spec: null, notes: [], standin: true, standin_reason: "No music key is set.", provider: null, conversion_id: null, job_id: null,
            asset: {
              id: "a1", kind: "audio", source: "standin", filename: "sketch.wav", media_type: "audio/wav", size_bytes: 10, sha256: "x", duration_s: 30,
              meta: {}, created_at: "", url: "/api/v1/assets/a1/file", standin: true,
            },
          },
          { kind: "conversion", text: null, spec: null, notes: [], asset: null, standin: false, standin_reason: null, provider: null, conversion_id: "c5", job_id: null },
        ],
      }),
    ]);
    renderChat();
    expect(await screen.findByRole("heading", { name: "Here's what I'll make" })).toBeInTheDocument();
    expect(screen.getByText("Assumed 30 seconds.")).toBeInTheDocument();
    expect(screen.getByText("Offline stand-in")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in the Notation Studio" })).toHaveAttribute("href", "/studio?conversion=c5");
    expect(screen.getByRole("button", { name: "Tracks" })).toHaveAttribute("aria-current", "true");
  });

  it("starts a thread about a loss report handed over in the URL, passing the context", async () => {
    nav.search = "conversion=c9&items=0,2";
    (api.sendMessage as Mock).mockImplementation(async function* () {
      yield event({ type: "delta", text: "The report says..." });
      yield event({ type: "done" });
    });
    vi.mocked(api.createThread).mockResolvedValue(thread({ id: "t7", title: "About a loss report" }));
    renderChat();
    await waitFor(() => expect(api.sendMessage).toHaveBeenCalledTimes(1));
    expect(api.createThread).toHaveBeenCalledWith("About a loss report");
    const [threadId, body] = (api.sendMessage as Mock).mock.calls[0];
    expect(threadId).toBe("t7");
    expect(body.context).toEqual({ conversion_id: "c9", item_indexes: [0, 2] });
    expect(body.text).toMatch(/loss report/);
    expect(nav.router.replace).toHaveBeenCalledWith("/chat", { scroll: false });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  });

  it("pre-attaches a track handed over in the URL", async () => {
    const user = userEvent.setup();
    nav.search = "asset=a1";
    vi.mocked(api.getAsset).mockResolvedValue({
      id: "a1", kind: "audio", source: "upload", filename: "clip.wav", media_type: "audio/wav", size_bytes: 10, sha256: "x", duration_s: 12,
      meta: {}, created_at: "", url: "/api/v1/assets/a1/file", standin: false,
    });
    (api.sendMessage as Mock).mockImplementation(async function* () {
      yield event({ type: "done" });
    });
    renderChat();
    expect(await screen.findByText("clip.wav is attached to your next message.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Attached tracks" })).getByText("clip.wav")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Message Orin"), "Transcribe this{Enter}");
    expect(api.sendMessage).toHaveBeenCalledWith("t1", { text: "Transcribe this", attachment_asset_ids: ["a1"] }, expect.any(AbortSignal));
  });

  it("creates, renames and deletes threads", async () => {
    const user = userEvent.setup();
    vi.mocked(api.threads).mockResolvedValue([thread({ id: "t1", title: "First" }), thread({ id: "t2", title: "Second" })]);
    vi.mocked(api.renameThread).mockResolvedValue(thread({ id: "t2", title: "Renamed" }));
    vi.mocked(api.deleteThread).mockResolvedValue({ ok: true });
    renderChat();
    const list = await screen.findByRole("navigation", { name: "Conversations" });
    expect(await within(list).findByRole("button", { name: "First" })).toHaveAttribute("aria-current", "true");

    await user.click(within(list).getByRole("button", { name: "Second" }));
    expect(api.messages).toHaveBeenLastCalledWith("t2");

    await user.click(within(list).getByRole("button", { name: "Rename Second" }));
    const field = within(list).getByLabelText("New name");
    await user.clear(field);
    await user.type(field, "Renamed{Enter}");
    expect(api.renameThread).toHaveBeenCalledWith("t2", "Renamed");
    expect(await within(list).findByRole("button", { name: "Renamed" })).toBeInTheDocument();

    await user.click(within(list).getByRole("button", { name: "Delete First" }));
    await user.click(within(list).getByRole("button", { name: "Yes, delete it" }));
    expect(api.deleteThread).toHaveBeenCalledWith("t1");
    await waitFor(() => expect(within(list).queryByRole("button", { name: "First" })).toBeNull());

    await user.click(within(list).getByRole("button", { name: "New chat" }));
    expect(screen.getByText("New conversation", { selector: "p" })).toBeInTheDocument();
  });
});
