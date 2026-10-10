import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { api, type Job } from "../../lib/api";
import { RegistryProvider } from "../shell/registry";
import { SessionProvider, type SessionValue } from "../shell/session";
import { StudioScreen } from "./StudioScreen";
import { makeConversion, makeRegistryFixture } from "./fixtures";

const nav = vi.hoisted(() => ({ search: "", router: { replace: vi.fn(), push: vi.fn() } }));

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.search),
  usePathname: () => "/studio",
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
      convert: vi.fn(),
      rerun: vi.fn(),
      watchJob: vi.fn(),
      cancelJob: vi.fn(),
      conversion: vi.fn(),
      explain: vi.fn(),
      getAsset: vi.fn(),
      listAssets: vi.fn(),
    },
  };
});

const job = (over: Partial<Job>): Job => ({
  id: "j1", type: "convert", status: "running", progress: 0, stage: null, message: null, error: null, result: null,
  created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-10T10:00:00Z", ...over,
});

const session: SessionValue = {
  user: { id: "u1", name: "Asha", created_at: "", prefs: { level: "beginner", language: "en", labels: "tradition", plain_language: true, notes: [] } },
  prefs: { level: "beginner", language: "en", labels: "tradition", plain_language: true, notes: [] },
  updatePrefs: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  prefsError: null,
};

function renderStudio() {
  return render(
    <RegistryProvider value={makeRegistryFixture()}>
      <SessionProvider value={session}>
        <StudioScreen />
      </SessionProvider>
    </RegistryProvider>,
  );
}

async function typeNotesAndConvert(user: ReturnType<typeof userEvent.setup>, notes: string) {
  await user.click(screen.getByRole("button", { name: "Typed notes" }));
  await user.type(screen.getByLabelText("Notes"), notes);
  await user.click(screen.getByRole("button", { name: "Convert" }));
}

beforeEach(() => {
  nav.search = "";
  nav.router.replace.mockReset();
  for (const fn of [api.convert, api.rerun, api.watchJob, api.cancelJob, api.conversion, api.explain, api.getAsset, api.listAssets]) {
    (fn as Mock).mockReset();
  }
  vi.mocked(api.listAssets).mockResolvedValue([]);
  vi.mocked(api.explain).mockResolvedValue({
    text: "A piano has one fixed pitch per key.", try_this: ["Try a slide on a violin."], source: "offline", cached: false, key: "k", level: "beginner",
  });
  (api.watchJob as Mock).mockImplementation(async (_id: string, onUpdate?: (j: Job) => void) => {
    onUpdate?.(job({ stage: "tonic", progress: 0.4 }));
    return job({ status: "done", progress: 1, result: { kind: "conversion", id: "conv1" } });
  });
});

describe("Notation Studio", () => {
  it("states the one-melody-line limit and waits for input", () => {
    renderStudio();
    expect(screen.getByRole("heading", { level: 1, name: "Notation Studio" })).toBeInTheDocument();
    expect(screen.getByText(/Orin writes one melody line at a time\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Convert" })).toBeDisabled();
    expect(screen.getByText("Only upload audio you have the right to use.")).toBeInTheDocument();
  });

  it("labels the action as clean-up when both selects hold the same format", async () => {
    const user = userEvent.setup();
    renderStudio();
    const output = screen.getByLabelText("Write the notation as") as HTMLSelectElement;
    await user.selectOptions(screen.getByLabelText("Read the input as"), output.value);
    expect(screen.getByRole("button", { name: "Clean up / quantize" })).toBeInTheDocument();
    expect(screen.getByText(/clean-up \/ quantize mode/)).toBeInTheDocument();
  });

  it("converts typed notes and shows the notation, the loss report and the explanation", async () => {
    const user = userEvent.setup();
    vi.mocked(api.convert).mockResolvedValue({ job_id: "j1", conversion_id: "conv1" });
    vi.mocked(api.conversion).mockResolvedValue(makeConversion({ notes_text: "D E F#" }));
    renderStudio();
    await typeNotesAndConvert(user, "D E F#");

    expect(api.convert).toHaveBeenCalledWith({ input_format: "auto", output_format: "fmt_staff", notes_text: "D E F#", notes_syntax: "auto" });
    expect(await screen.findByRole("heading", { name: "Notation: Staff Notation" })).toBeInTheDocument();
    expect(nav.router.replace).toHaveBeenCalledWith("/studio?conversion=conv1", { scroll: false });

    // Detection: three candidates with percentages.
    expect(screen.getByText("Detected: Swara Notation (62%), Staff Notation (27%), Pop Lead Sheet (11%).")).toBeInTheDocument();
    // The tonic, prominently, with its nearest note.
    expect(screen.getByText("146.8 Hz", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText(/nearest note D3/)).toBeInTheDocument();
    // Mode lookalikes: exact ones listed, rotations collapsed.
    expect(screen.getByText("also a rotation of 2 others")).toBeInTheDocument();
    // The description is always shown.
    expect(screen.getByText("Three notes rising from D.")).toBeInTheDocument();
    // Loss and explanation.
    expect(screen.getByRole("heading", { name: "Loss report" })).toBeInTheDocument();
    expect(await screen.findByText("A piano has one fixed pitch per key.")).toBeInTheDocument();
    expect(screen.getByText("Offline stand-in: Orin's built-in text")).toBeInTheDocument();
    expect(api.explain).toHaveBeenCalledWith({ conversion_id: "conv1", level: "beginner" });
    // The low-confidence note is marked in the text notation.
    expect(document.querySelector('[data-note-id="n2"]')).toHaveAttribute("data-low", "true");
    // Handoffs.
    expect(screen.getByRole("link", { name: "Open in Workspace" })).toHaveAttribute("href", "/workspace?import_conversion=conv1");
    expect(screen.getByRole("link", { name: "Ask Orin about this loss report" })).toHaveAttribute("href", "/chat?conversion=conv1");
    expect(screen.getByText("Done: 3 notes written as Staff Notation.")).toHaveAttribute("role", "status");
  });

  it("highlights a loss item's note and asks the piano question about it", async () => {
    const user = userEvent.setup();
    vi.mocked(api.convert).mockResolvedValue({ job_id: "j1", conversion_id: "conv1" });
    vi.mocked(api.conversion).mockResolvedValue(makeConversion({ notes_text: "D E F#" }));
    renderStudio();
    await typeNotesAndConvert(user, "D E F#");
    await screen.findByText("A piano has one fixed pitch per key.");

    await user.click(screen.getByRole("button", { name: /rounded by 31 cents/ }));
    expect(document.querySelector('[data-note-id="n2"]')).toHaveAttribute("data-selected", "true");
    expect(screen.getByRole("link", { name: "Ask Orin about this loss report" })).toHaveAttribute("href", "/chat?conversion=conv1&items=0");

    await user.click(screen.getByRole("button", { name: "Why can't a piano play this?" }));
    await waitFor(() =>
      expect(api.explain).toHaveBeenLastCalledWith({ conversion_id: "conv1", level: "beginner", item_indexes: [0], question: "Why can't a piano play this?" }),
    );
  });

  it("asks the user to choose when auto-detect is not sure, and re-runs with the choice", async () => {
    const user = userEvent.setup();
    vi.mocked(api.convert).mockResolvedValue({ job_id: "j1", conversion_id: "conv1" });
    (api.watchJob as Mock).mockResolvedValueOnce(job({ status: "awaiting_input", progress: 0.5 }));
    vi.mocked(api.conversion).mockResolvedValueOnce(
      makeConversion({ status: "awaiting_style", needs_style_choice: true, source_format: null, ir: null, score: null, loss: null, outputs: [], detect_message: "The top two are close." }),
    );
    vi.mocked(api.rerun).mockResolvedValue({ job_id: "j2", conversion_id: "conv1" });
    renderStudio();
    await typeNotesAndConvert(user, "D E F#");

    expect(await screen.findByRole("heading", { name: "Not sure - please choose" })).toBeInTheDocument();
    // Spec 8.2: the dropdown is prefilled with the top candidate.
    expect((screen.getByLabelText("Read the input as") as HTMLSelectElement).value).toBe("fmt_swara");

    vi.mocked(api.conversion).mockResolvedValue(makeConversion({ input_format: "fmt_staff", source_format: "fmt_staff" }));
    const choices = within(screen.getByRole("group", { name: "Detected style candidates" }));
    await user.click(choices.getByRole("button", { name: /Staff Notation/ }));
    expect(api.rerun).toHaveBeenCalledWith("conv1", { input_format: "fmt_staff" });
    expect(await screen.findByRole("heading", { name: "Notation: Staff Notation" })).toBeInTheDocument();
  });

  it("shows a failed conversion with its parse problems by line and column", async () => {
    const user = userEvent.setup();
    vi.mocked(api.convert).mockResolvedValue({ job_id: "j1", conversion_id: "conv1" });
    (api.watchJob as Mock).mockResolvedValue(job({ status: "failed", error: { code: "parse", message: "The notes could not be read.", hint: null, retryable: false } }));
    vi.mocked(api.conversion).mockResolvedValue(
      makeConversion({
        status: "failed", ir: null, score: null, loss: null, outputs: [], detected: [], notes_text: "S R X G",
        error: { code: "parse", message: "The notes could not be read.", hint: "Check the marked places.", retryable: false },
        parse_problems: [{ code: "bad_token", message: "X is not a note name.", line: 1, column: 5, length: 1, severity: "error" }],
      }),
    );
    renderStudio();
    await typeNotesAndConvert(user, "S R X G");

    const alert = await screen.findByText("This conversion failed.");
    expect(alert.closest('[role="alert"]')).toHaveTextContent("The notes could not be read.");
    expect(screen.getByText("Check the marked places.")).toBeInTheDocument();
    expect(screen.getByText("Error, line 1, column 5:")).toBeInTheDocument();
    expect(screen.getByText("X is not a note name.")).toBeInTheDocument();
    const mark = document.querySelector("mark") as HTMLElement;
    expect(mark).toHaveTextContent("X");
    expect(mark).toHaveAttribute("title", "X is not a note name.");
    expect(screen.queryByRole("heading", { name: /Notation: / })).toBeNull();
  });

  it("shows a request error with a retry instead of failing silently", async () => {
    const user = userEvent.setup();
    vi.mocked(api.convert).mockRejectedValueOnce(new Error("Can't reach the Orin server."));
    renderStudio();
    await typeNotesAndConvert(user, "D E");
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the Orin server.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("opens a conversion handed over in the URL", async () => {
    nav.search = "conversion=conv1";
    vi.mocked(api.conversion).mockResolvedValue(makeConversion({ notes_text: "D E F#", input_format: "fmt_swara" }));
    renderStudio();
    expect(await screen.findByRole("heading", { name: "Notation: Staff Notation" })).toBeInTheDocument();
    expect(api.conversion).toHaveBeenCalledWith("conv1");
    expect((screen.getByLabelText("Read the input as") as HTMLSelectElement).value).toBe("fmt_swara");
    expect((screen.getByLabelText("Notes") as HTMLTextAreaElement).value).toBe("D E F#");
  });

  it("says which renderer is missing and offers the alternative", async () => {
    nav.search = "conversion=conv1";
    vi.mocked(api.conversion).mockResolvedValue(
      makeConversion({ missing: [{ kind: "svg", message: "The staff renderer is not installed.", alternative: "names" }] }),
    );
    renderStudio();
    expect(await screen.findByText("Staff is not available.")).toBeInTheDocument();
    expect(screen.getByText("The staff renderer is not installed.")).toBeInTheDocument();
    expect(screen.getByText("Closest alternative: Note names.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show Note names" })).toBeInTheDocument();
  });
});
