import { expect, test, type Page } from "@playwright/test";

// Smoke tests for the three screens (spec 13.7), against the real API in offline mode.
// Each test uses its own profile, so runs do not see each other's data.

const SARGAM = [
  "tonic: D3",
  "raga: mohanam",
  "tala: adi",
  "tempo: 80",
  "units_per_beat: 2",
  "",
  "S R2 G3 P D2 S' , , | D2 P G3 R2 | S , , , ||",
  "G3~ , P , D2 P G3 R2 | S R2 G3/ P | D2 , P , ||",
].join("\n");

const API = process.env.ORIN_E2E_API ?? "http://127.0.0.1:8000";

/**
 * These tests must never spend real provider credits. If the API they would talk to has
 * live keys loaded (for example a dev server you already had running), they skip.
 */
async function requireOfflineApi(page: Page) {
  const health = await (await page.request.get(`${API}/api/v1/health`)).json();
  const live = !(health.offline.llm && health.offline.music && health.offline.tts);
  test.skip(live, "The running API has live keys. Stop it, or start it with ORIN_OFFLINE=1, and run again.");
}

async function signIn(page: Page, path: string) {
  await requireOfflineApi(page);
  await page.goto(path);
  await page.getByLabel("Profile name").fill(`e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("navigation").getByRole("link", { name: "Notation Studio" })).toBeVisible();
}

test("Notation Studio: typed sargam becomes staff notation with a loss report", async ({ page }) => {
  await signIn(page, "/studio");

  // The dropdowns come from the registry and say that the tables are unreviewed.
  await page.getByLabel("Read the input as").selectOption({ label: "Carnatic (Sangeetham)" });
  await expect(page.getByText("Unreviewed draft").first()).toBeVisible();

  await page.getByRole("button", { name: "Typed notes" }).click();
  await page.getByPlaceholder("Type or paste notes here").fill(SARGAM);
  await page.getByRole("button", { name: "Convert" }).click();

  await expect(page.getByText(/Done: \d+ notes written as Western/)).toBeVisible();
  await expect(page.locator("svg g.note").first()).toBeVisible(); // the staff was drawn
  await expect(page.getByRole("heading", { name: "Loss report" })).toBeVisible();
  await expect(page.getByText(/Fidelity/).first()).toBeVisible();
  await expect(page.locator("summary", { hasText: "Ornament dropped" })).toBeVisible(); // the oscillation has no staff symbol

  // The same score as text, for readers who do not use the staff.
  await page.getByRole("tab", { name: "Note names" }).click();
  await expect(page.getByText(/F#3/).first()).toBeVisible();
});

test("Notation Studio: a mistake in typed notes is reported by line and column", async ({ page }) => {
  await signIn(page, "/studio");
  await page.getByLabel("Read the input as").selectOption({ label: "Carnatic (Sangeetham)" });
  await page.getByRole("button", { name: "Typed notes" }).click();
  await page.getByPlaceholder("Type or paste notes here").fill("raga: mohanam\nS R2 G3 X P");
  await page.getByRole("button", { name: "Convert" }).click();
  await expect(page.getByText(/line 2/i).first()).toBeVisible();
  await expect(page.getByText(/column 9/i).first()).toBeVisible();
});

test("Chat: a request becomes a spec card, then a labelled stand-in track", async ({ page }) => {
  await signIn(page, "/chat");
  await page.getByLabel("Message Orin").fill("Make a short Carnatic alapana that turns into jazz piano.");
  await page.getByRole("button", { name: "Send" }).click();

  // Orin shows what it will make before making it.
  const generate = page.getByRole("button", { name: "Generate" });
  await expect(generate).toBeVisible();
  await expect(page.getByText(/jazz piano/i).first()).toBeVisible();
  await generate.click();

  // With no keys the track is a synthesised stand-in, and says so.
  await expect(page.getByText("Offline stand-in").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "Open in Workspace" }).or(page.getByRole("button", { name: "Open in Workspace" }))).toBeVisible();
});

test("Workspace: a drum pattern plays and exports as WAV", async ({ page }) => {
  await signIn(page, "/workspace");
  await page.getByRole("button", { name: "+ Drum track" }).click();
  await page.getByRole("tab", { name: "Step sequencer" }).click();

  const kick = page.getByRole("button", { name: "Kick step 1", exact: true });
  await kick.click();
  await expect(kick).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Snare step 5", exact: true }).click();

  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "Stop", exact: true }).click();

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export WAV" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.wav$/);

  // Undo takes the last change back.
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByRole("button", { name: "Snare step 5", exact: true })).toHaveAttribute("aria-pressed", "false");
});

test("Notation Studio: downloads work and the result hands over to the Workspace and the Chat", async ({ page }) => {
  await signIn(page, "/studio");
  await page.getByLabel("Read the input as").selectOption({ label: "Carnatic (Sangeetham)" });
  await page.getByRole("button", { name: "Typed notes" }).click();
  await page.getByPlaceholder("Type or paste notes here").fill(SARGAM);
  await page.getByRole("button", { name: "Convert" }).click();
  await expect(page.getByText(/Done: \d+ notes written as Western/)).toBeVisible();

  const downloads = page.getByRole("region", { name: "Downloads" });
  for (const [button, ext] of [
    ["MIDI .mid", /\.mid$/],
    ["Staff image .png", /\.png$/],
    ["Analysis data (IR) .json", /-ir\.json$/],
    ["Loss report .txt", /\.txt$/],
  ] as const) {
    const download = page.waitForEvent("download");
    await downloads.getByRole("button", { name: button, exact: true }).click();
    expect((await download).suggestedFilename()).toMatch(ext);
  }

  // Notation -> Workspace: the score arrives as a piano-roll clip.
  await downloads.getByRole("link", { name: "Open in Workspace" }).click();
  await expect(page.getByText(/Brought in \d+ notes from the Notation Studio/)).toBeVisible();

  // Notation -> Chat: the loss report becomes the subject of a new conversation.
  await page.goBack();
  await page.getByRole("region", { name: "Downloads" }).getByRole("link", { name: "Ask Orin about this loss report" }).click();
  await expect(page).toHaveURL(/\/chat/);
  await expect(page.getByLabel("Message Orin")).toBeVisible();
});

test("Home: explains the idea without signing in and leads into the app", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("One melody.");

  // The one interaction: the same melody as performed, then as piano keys hold it.
  await expect(page.getByText("This is the performance.")).toBeVisible();
  await page.getByRole("button", { name: "On piano keys" }).click();
  await expect(page.getByText("2 things did not make it onto the keys.")).toBeVisible();
  await page.getByRole("button", { name: "Play this version" }).click();

  // The traditions come from the server's registry and are marked as drafts.
  await page.getByRole("button", { name: "Arabic Maqam" }).click();
  await expect(page.getByText("Unreviewed draft").first()).toBeVisible();

  // Into the app: the profile picker appears only now.
  await page.getByRole("region", { name: "Ask, write, make" }).getByRole("link", { name: /Notation Studio/ }).click();
  await expect(page).toHaveURL(/\/studio/);
  await expect(page.getByLabel("Profile name")).toBeVisible();
});
