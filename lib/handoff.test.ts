import { describe, expect, it } from "vitest";
import { handoff, readHandoff } from "./handoff";

describe("handoff URLs", () => {
  it("round-trips a loss-report handoff to chat", () => {
    const url = new URL(handoff.chatAboutLoss("c1", [0, 3]), "http://x");
    expect(readHandoff(url.searchParams)).toEqual({
      importAsset: undefined, importConversion: undefined, asset: undefined, conversion: "c1", items: [0, 3],
    });
  });
  it("round-trips a notation handoff to the workspace", () => {
    const url = new URL(handoff.workspaceWithConversion("c 2"), "http://x");
    expect(readHandoff(url.searchParams).importConversion).toBe("c 2");
  });
});
