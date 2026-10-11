import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, getToken, type Asset, type User } from "../../lib/api";
import { makeRegistryFixture } from "../studio/fixtures";
import { Dialog } from "./Dialog";
import { GlossaryProvider, Term } from "./Glossary";
import { RegistryProvider } from "./registry";
import { SessionGate, useSession } from "./session";
import { Shell, StatusStrip } from "./Shell";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/studio" }));

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return {
    ...actual,
    getToken: vi.fn(() => null),
    onAuthChange: vi.fn(() => () => undefined),
    api: {
      ...actual.api, me: vi.fn(), signIn: vi.fn(), signOut: vi.fn(), formats: vi.fn(), glossary: vi.fn(), listAssets: vi.fn(),
      deleteAsset: vi.fn(), updatePrefs: vi.fn(), uploadAsset: vi.fn(),
    },
  };
});

const user: User = {
  id: "u1",
  name: "Asha",
  created_at: "2026-10-10T10:00:00Z",
  prefs: { level: "beginner", language: "en", labels: "tradition", plain_language: true, notes: [] },
};

function Who() {
  const session = useSession();
  return (
    <p>
      {session.user.name} / {session.prefs.level}
    </p>
  );
}

beforeEach(() => {
  vi.mocked(getToken).mockReset().mockReturnValue(null);
  vi.mocked(api.me).mockReset();
  vi.mocked(api.signIn).mockReset();
  vi.mocked(api.formats).mockReset();
  vi.mocked(api.glossary).mockReset().mockResolvedValue([{ term: "raga", definition: "A melodic framework.", tradition: "Indian classical" }]);
  vi.mocked(api.listAssets).mockReset().mockResolvedValue([]);
  vi.mocked(api.deleteAsset).mockReset().mockResolvedValue({ ok: true });
  vi.mocked(api.updatePrefs).mockReset();
  vi.mocked(api.uploadAsset).mockReset();
});

const asset = (over: Partial<Asset>): Asset => ({
  id: "a1", kind: "audio", source: "upload", filename: "clip.wav", media_type: "audio/wav", size_bytes: 2048, sha256: "x",
  duration_s: 12, meta: {}, created_at: "2026-10-10T10:00:00Z", url: "/api/v1/assets/a1/file", standin: false, ...over,
});

describe("shell", () => {
  async function renderShell() {
    vi.mocked(getToken).mockReturnValue("tok");
    vi.mocked(api.me).mockResolvedValue(user);
    vi.mocked(api.formats).mockResolvedValue(makeRegistryFixture());
    render(
      <Shell>
        <h1>Screen</h1>
      </Shell>,
    );
    await screen.findByRole("heading", { name: "Screen" });
  }

  it("has a skip link, the three areas with the current one marked, and a way back to the sketch", async () => {
    await renderShell();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#orin-main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "orin-main");
    const nav = screen.getByRole("navigation", { name: "Orin areas" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")])).toEqual([
      ["Chat", "/chat", null],
      ["Notation Studio", "/studio", "page"],
      ["Workspace", "/workspace", null],
    ]);
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
    expect(screen.getByText("Asha")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("stores the learner level and the note-name preference", async () => {
    const actor = userEvent.setup();
    vi.mocked(api.updatePrefs).mockImplementation(async (prefs) => ({ ...user, prefs: { ...user.prefs, ...prefs } }));
    await renderShell();
    await actor.selectOptions(screen.getByLabelText("Explanation level"), "Advanced");
    expect(api.updatePrefs).toHaveBeenLastCalledWith({ level: "advanced" });
    await waitFor(() => expect((screen.getByLabelText("Explanation level") as HTMLSelectElement).value).toBe("advanced"));
    await actor.selectOptions(screen.getByLabelText("Note names"), "Do Re Mi");
    expect(api.updatePrefs).toHaveBeenLastCalledWith({ labels: "solfege" });
  });

  it("opens the Library grouped by source, with send-to links, download and a confirmed delete", async () => {
    const actor = userEvent.setup();
    vi.mocked(api.listAssets).mockResolvedValue([
      asset({}),
      asset({ id: "a2", source: "generated", filename: "made.wav", standin: true }),
      asset({ id: "a3", source: "conversion", kind: "midi", filename: "tune.mid", duration_s: null }),
    ]);
    await renderShell();
    await actor.click(screen.getByRole("button", { name: "Library" }));
    const panel = await screen.findByRole("dialog", { name: "Library" });
    expect(within(panel).getByText("Only upload audio you have the right to use.")).toBeInTheDocument();
    for (const [name, count] of [["Uploads", 1], ["Generated", 1], ["Bounces", 0], ["Conversions", 1]] as const) {
      expect(await within(panel).findByRole("heading", { name: `${name} (${count})` })).toBeInTheDocument();
    }
    expect(within(panel).getByText("Offline stand-in")).toBeInTheDocument();

    const row = within(panel).getByText("clip.wav").closest("li") as HTMLElement;
    expect(within(row).getByRole("button", { name: "Play clip.wav" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Download clip.wav" })).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Notation Studio" })).toHaveAttribute("href", "/studio?asset=a1");
    expect(within(row).getByRole("link", { name: "Workspace" })).toHaveAttribute("href", "/workspace?import_asset=a1");
    expect(within(row).getByRole("link", { name: "Chat" })).toHaveAttribute("href", "/chat?asset=a1");
    // A MIDI file cannot be played here or sent to the chat.
    const midi = within(panel).getByText("tune.mid").closest("li") as HTMLElement;
    expect(within(midi).queryByRole("button", { name: /Play/ })).toBeNull();
    expect(within(midi).queryByRole("link", { name: "Chat" })).toBeNull();

    await actor.click(within(row).getByRole("button", { name: "Delete clip.wav" }));
    expect(api.deleteAsset).not.toHaveBeenCalled();
    await actor.click(within(row).getByRole("button", { name: "Yes, delete clip.wav" }));
    expect(api.deleteAsset).toHaveBeenCalledWith("a1");

    await actor.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the glossary from the navigation", async () => {
    const actor = userEvent.setup();
    await renderShell();
    await actor.click(screen.getByRole("button", { name: "Glossary" }));
    const dialog = await screen.findByRole("dialog", { name: "Glossary" });
    expect(within(dialog).getByText("raga")).toBeInTheDocument();
    expect(within(dialog).getByText("A melodic framework.")).toBeInTheDocument();
  });
});

describe("session gate", () => {
  it("shows the profile picker without a token and says it is a demo profile", async () => {
    render(
      <SessionGate>
        <p>Inside the app</p>
      </SessionGate>,
    );
    expect(await screen.findByRole("heading", { name: "Choose a profile" })).toBeInTheDocument();
    expect(screen.getByLabelText("Profile name")).toBeInTheDocument();
    expect(screen.getByText(/This is a demo profile with no password/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(screen.queryByText("Inside the app")).toBeNull();
    expect(api.me).not.toHaveBeenCalled();
  });

  it("signs in by name and then gives the app the user and prefs", async () => {
    const actor = userEvent.setup();
    vi.mocked(api.signIn).mockResolvedValue({ token: "tok", user });
    render(
      <SessionGate>
        <Who />
      </SessionGate>,
    );
    await actor.type(await screen.findByLabelText("Profile name"), "Asha");
    await actor.click(screen.getByRole("button", { name: "Continue" }));
    expect(api.signIn).toHaveBeenCalledWith("Asha");
    expect(await screen.findByText("Asha / beginner")).toBeInTheDocument();
  });

  it("opens the stored profile when there is a token", async () => {
    vi.mocked(getToken).mockReturnValue("tok");
    vi.mocked(api.me).mockResolvedValue(user);
    render(
      <SessionGate>
        <Who />
      </SessionGate>,
    );
    expect(await screen.findByText("Asha / beginner")).toBeInTheDocument();
  });

  it("shows the error and a retry when the server cannot be reached", async () => {
    vi.mocked(getToken).mockReturnValue("tok");
    vi.mocked(api.me).mockRejectedValue(new Error("Can't reach the Orin server."));
    render(
      <SessionGate>
        <Who />
      </SessionGate>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the Orin server.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("registry and status strip", () => {
  it("says in words which services are on their offline stand-ins", () => {
    render(
      <RegistryProvider value={makeRegistryFixture()}>
        <StatusStrip />
      </RegistryProvider>,
    );
    const strip = screen.getByRole("complementary", { name: "Service status" });
    expect(strip).toHaveTextContent("Gemini is on its offline stand-in");
    expect(strip).toHaveTextContent("Music generation is on its offline stand-in");
    expect(strip).toHaveTextContent("Narration is on its offline stand-in");
    expect(strip).toHaveTextContent("Why: No keys set");
  });

  it("shows nothing when every service is live", () => {
    const registry = { ...makeRegistryFixture(), offline: { llm: false, music: false, tts: false, reason: null } };
    const { container } = render(
      <RegistryProvider value={registry}>
        <StatusStrip />
      </RegistryProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the error with a retry when the registry cannot be loaded", async () => {
    const actor = userEvent.setup();
    vi.mocked(api.formats).mockRejectedValueOnce(new Error("Can't reach the Orin server.")).mockResolvedValueOnce(makeRegistryFixture());
    render(
      <RegistryProvider>
        <p>Loaded</p>
      </RegistryProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the Orin server.");
    await actor.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Loaded")).toBeInTheDocument();
  });
});

describe("glossary term", () => {
  it("shows its definition on focus, ties it with aria-describedby, and hides it on Escape", async () => {
    const actor = userEvent.setup();
    render(
      <GlossaryProvider entries={[{ term: "tonic", definition: "The home note of a piece.", tradition: null }]}>
        <p>
          Measured from the <Term name="Tonic">tonic</Term>.
        </p>
      </GlossaryProvider>,
    );
    const word = screen.getByRole("button", { name: "tonic" });
    const tip = screen.getByRole("tooltip", { hidden: true });
    expect(tip).toHaveTextContent("The home note of a piece.");
    expect(word).toHaveAttribute("aria-describedby", tip.id);
    expect(tip).not.toBeVisible();

    await actor.tab();
    expect(word).toHaveFocus();
    expect(tip).toBeVisible();

    await actor.keyboard("{Escape}");
    expect(tip).not.toBeVisible();
  });

  it("renders plain text for a word the glossary does not have", () => {
    render(
      <GlossaryProvider entries={[]}>
        <p>
          A <Term name="mystery">mystery</Term> word
        </p>
      </GlossaryProvider>,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/mystery/)).toBeInTheDocument();
  });
});

describe("dialog", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>
          Open
        </button>
        {open && (
          <Dialog title="Glossary" onClose={() => setOpen(false)}>
            <button type="button">First</button>
            <button type="button">Last</button>
          </Dialog>
        )}
      </>
    );
  }

  it("takes focus, keeps Tab inside, closes on Escape and gives focus back", async () => {
    const actor = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await actor.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Glossary" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();

    await actor.tab();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    await actor.tab();
    await actor.tab();
    expect(screen.getByRole("button", { name: "Last" })).toHaveFocus();
    await actor.tab();
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    await actor.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Last" })).toHaveFocus();

    await actor.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });
});
