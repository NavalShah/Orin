import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeRegistry } from "../shell/registry";
import { DetectionBanner } from "./DetectionBanner";
import { makeRegistryFixture } from "./fixtures";

const { formatName } = makeRegistry(makeRegistryFixture());
const detected = [
  { format_id: "fmt_swara", score: 0.62 },
  { format_id: "fmt_staff", score: 0.27 },
  { format_id: "fmt_pop", score: 0.11 },
  { format_id: "fmt_extra", score: 0.0 },
];

describe("detection banner", () => {
  it("shows the top three candidates with percentages, never a bare label", () => {
    render(
      <DetectionBanner detected={detected} needsChoice={false} sourceFormat="fmt_swara" formatName={formatName} limits={["Short clips are ambiguous."]} onChoose={vi.fn()} />,
    );
    expect(screen.getByRole("heading", { name: "Detected style" })).toBeInTheDocument();
    expect(screen.getByText("Detected: Swara Notation (62%), Staff Notation (27%), Pop Lead Sheet (11%).")).toBeInTheDocument();
    const buttons = within(screen.getByRole("group", { name: "Detected style candidates" })).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Swara Notation 62%", "Staff Notation 27%", "Pop Lead Sheet 11%"]);
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Short clips are ambiguous.")).toBeInTheDocument();
  });

  it("says it is not sure and asks the user to choose", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    render(
      <DetectionBanner
        detected={detected}
        needsChoice
        message="The top two styles are 4 points apart."
        formatName={formatName}
        limits={[]}
        onChoose={onChoose}
      />,
    );
    expect(screen.getByRole("heading", { name: "Not sure - please choose" })).toBeInTheDocument();
    // The server's sentence is shown as given; the three candidates stay visible as buttons with their scores.
    expect(screen.getByText("The top two styles are 4 points apart.")).toBeInTheDocument();
    expect(screen.queryByText(/^Closest styles:/)).not.toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Detected style candidates" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Swara Notation 62%", "Staff Notation 27%", "Pop Lead Sheet 11%",
    ]);
    // Nothing is presented as already chosen.
    for (const b of within(screen.getByRole("group", { name: "Detected style candidates" })).getAllByRole("button")) {
      expect(b).toHaveAttribute("aria-pressed", "false");
    }
    await user.click(screen.getByRole("button", { name: /Staff Notation/ }));
    expect(onChoose).toHaveBeenCalledWith("fmt_staff");
  });

  it("renders nothing without candidates", () => {
    const { container } = render(<DetectionBanner detected={[]} needsChoice={false} formatName={formatName} limits={[]} onChoose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
