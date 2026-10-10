import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { FormatsResponse } from "../../lib/api";
import { FormatSelects } from "./FormatSelects";
import { makeFormat, makeRegistryFixture } from "./fixtures";

function setup(registry: FormatsResponse = makeRegistryFixture(), input = "auto", output = "fmt_swara") {
  const onInputChange = vi.fn();
  const onOutputChange = vi.fn();
  render(
    <FormatSelects
      registry={registry}
      inputFormat={input}
      outputFormat={output}
      onInputChange={onInputChange}
      onOutputChange={onOutputChange}
    />,
  );
  return {
    onInputChange,
    onOutputChange,
    input: screen.getByLabelText("Read the input as") as HTMLSelectElement,
    output: screen.getByLabelText("Write the notation as") as HTMLSelectElement,
  };
}

const groupsOf = (select: HTMLSelectElement) =>
  Array.from(select.querySelectorAll("optgroup")).map((g) => ({
    label: g.getAttribute("label"),
    options: Array.from(g.querySelectorAll("option")).map((o) => o.textContent),
  }));

describe("format selects", () => {
  it("are built from the registry and grouped by family", () => {
    const { input, output } = setup();
    const expected = [
      { label: "Western", options: ["Staff Notation", "Pop Lead Sheet"] },
      { label: "South Asian", options: ["Swara Notation"] },
    ];
    expect(groupsOf(output)).toEqual(expected);
    expect(groupsOf(input)).toEqual(expected);
  });

  it("offer Auto-detect on the input side only", () => {
    const { input, output } = setup();
    expect(within(input).getByRole("option", { name: "Auto-detect" })).toBeInTheDocument();
    expect(input.options[0].value).toBe("auto");
    expect(within(output).queryByRole("option", { name: "Auto-detect" })).toBeNull();
  });

  it("show the chosen format's review status in words and its caveats", () => {
    setup(makeRegistryFixture(), "fmt_staff", "fmt_swara");
    expect(screen.getByText("Unreviewed draft")).toBeInTheDocument();
    expect(screen.getByText("Self-checked draft")).toBeInTheDocument();
    expect(screen.getByText("Caveats for Swara Notation (2)")).toBeInTheDocument();
    expect(screen.getByText("The raga table is a draft.")).toBeInTheDocument();
    expect(screen.getByText("Gamaka marks are approximate.")).toBeInTheDocument();
    expect(screen.getByText("Pitches between the keys are rounded.")).toBeInTheDocument();
    expect(screen.getByText("Review note: Checked against two textbooks only.")).toBeInTheDocument();
  });

  it("ties the status badge to its select", () => {
    const { output } = setup();
    const badge = document.getElementById(output.getAttribute("aria-describedby") ?? "");
    expect(badge).toHaveTextContent("Unreviewed draft");
  });

  it("show a format added to the registry with no code change", () => {
    const registry = makeRegistryFixture([
      makeFormat({
        id: "fmt_brand_new",
        display_name: "Brand New Tradition",
        family: "fam_new",
        family_label: "New Family",
        review_status: "practitioner_reviewed",
        caveats: ["A caveat nobody has seen before."],
      }),
    ]);
    const { input, output } = setup(registry, "auto", "fmt_brand_new");
    for (const select of [input, output]) {
      expect(groupsOf(select)).toContainEqual({ label: "New Family", options: ["Brand New Tradition"] });
    }
    expect(output.value).toBe("fmt_brand_new");
    expect(screen.getByText("Reviewed by a practitioner")).toBeInTheDocument();
    expect(screen.getByText("A caveat nobody has seen before.")).toBeInTheDocument();
  });

  it("still offers a format whose family is not listed", () => {
    const registry = makeRegistryFixture();
    registry.formats.push(makeFormat({ id: "fmt_stray", display_name: "Stray Format", family: "nowhere", family_label: "Nowhere" }));
    const { output } = setup(registry);
    expect(groupsOf(output)).toContainEqual({ label: "Other", options: ["Stray Format"] });
  });

  it("reports the chosen ids", async () => {
    const user = userEvent.setup();
    const { input, output, onInputChange, onOutputChange } = setup();
    await user.selectOptions(output, "Pop Lead Sheet");
    expect(onOutputChange).toHaveBeenCalledWith("fmt_pop");
    await user.selectOptions(input, "Swara Notation");
    expect(onInputChange).toHaveBeenCalledWith("fmt_swara");
  });
});
