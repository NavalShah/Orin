import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { newProject, type Project } from "../../lib/workspace/project";
import { reduce } from "../../lib/workspace/reducer";
import { StepSequencer } from "./StepSequencer";

/** The sequencer on top of the real reducer, the way the Workspace wires it. */
function Harness() {
  const [project, setProject] = useState<Project>(() => reduce(newProject(), { type: "addTrack", kind: "drums", id: "d1" }));
  return (
    <StepSequencer
      drums={project.drums}
      onToggle={(voice, step) => setProject((p) => reduce(p, { type: "toggleStep", voice, step }))}
      onGain={(voice, gain) => setProject((p) => reduce(p, { type: "setDrumRowGain", voice, gain }))}
      onPan={(voice, pan) => setProject((p) => reduce(p, { type: "setDrumRowPan", voice, pan }))}
      onAddDrums={() => undefined}
    />
  );
}

describe("StepSequencer", () => {
  it("toggles a step by keyboard and exposes its state with aria-pressed", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const step = screen.getByRole("button", { name: "Kick step 1" });
    expect(step).toHaveAttribute("aria-pressed", "false");

    step.focus();
    await user.keyboard("{Enter}");
    expect(step).toHaveAttribute("aria-pressed", "true");
    await user.keyboard(" ");
    expect(step).toHaveAttribute("aria-pressed", "false");
    await user.keyboard(" ");
    expect(step).toHaveAttribute("aria-pressed", "true");

    // Other steps are untouched.
    expect(screen.getByRole("button", { name: "Kick step 2" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Snare step 1" })).toHaveAttribute("aria-pressed", "false");
  });

  it("moves between steps with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    screen.getByRole("button", { name: "Kick step 1" }).focus();
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowDown}");
    expect(screen.getByRole("button", { name: "Snare step 3" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Snare step 3" })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("{End}");
    expect(screen.getByRole("button", { name: "Snare step 16" })).toHaveFocus();
  });

  it("labels every control and offers to add a drum track when there is none", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Harness />);
    expect(screen.getAllByRole("button", { pressed: false })).toHaveLength(64);
    expect(screen.getByRole("slider", { name: "Hi-hat volume" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Clap pan" })).toBeInTheDocument();
    unmount();

    const onAddDrums = vi.fn();
    render(<StepSequencer drums={null} onToggle={() => undefined} onGain={() => undefined} onPan={() => undefined} onAddDrums={onAddDrums} />);
    await user.click(screen.getByRole("button", { name: "Add drum track" }));
    expect(onAddDrums).toHaveBeenCalledOnce();
  });
});
