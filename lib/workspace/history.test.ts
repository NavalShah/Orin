import { describe, expect, it } from "vitest";
import * as H from "./history";

describe("history", () => {
  it("keeps at least 50 undo steps after 60 actions", () => {
    let h = H.createHistory(0);
    for (let i = 1; i <= 60; i++) h = H.push(h, i);
    expect(h.present).toBe(60);
    expect(h.past.length).toBeGreaterThanOrEqual(50);
    for (let i = 0; i < 50; i++) h = H.undo(h);
    expect(h.present).toBe(10);
    expect(H.canRedo(h)).toBe(true);
  });

  it("caps the stack at the limit", () => {
    let h = H.createHistory(0);
    for (let i = 1; i <= 250; i++) h = H.push(h, i);
    expect(h.past.length).toBe(H.HISTORY_LIMIT);
    expect(h.past[h.past.length - 1]).toBe(249);
  });

  it("undo then redo returns to the same state", () => {
    let h = H.push(H.push(H.createHistory("a"), "b"), "c");
    h = H.undo(h);
    expect(h.present).toBe("b");
    h = H.redo(h);
    expect(h.present).toBe("c");
    expect(H.canRedo(h)).toBe(false);
  });

  it("a new action after undo clears redo", () => {
    let h = H.push(H.push(H.createHistory("a"), "b"), "c");
    h = H.undo(h);
    expect(h.future).toEqual(["c"]);
    h = H.push(h, "d");
    expect(h.future).toEqual([]);
    expect(H.redo(h)).toBe(h);
    expect(H.undo(h).present).toBe("b");
  });

  it("a drag commits one step", () => {
    let h = H.push(H.createHistory(0), 1);
    const before = h.past.length;
    h = H.begin(h);
    for (let x = 2; x <= 30; x++) h = H.preview(h, x);
    expect(h.present).toBe(30);
    expect(h.past.length).toBe(before); // nothing recorded while dragging
    h = H.commit(h);
    expect(h.past.length).toBe(before + 1);
    expect(h.base).toBeNull();
    h = H.undo(h);
    expect(h.present).toBe(1); // the whole drag is undone at once
    expect(H.redo(h).present).toBe(30);
  });

  it("a drag that ends where it began records nothing, and cancel restores", () => {
    let h = H.createHistory(5);
    h = H.commit(H.begin(h));
    expect(h.past).toEqual([]);
    h = H.preview(H.begin(h), 9);
    h = H.cancel(h);
    expect(h.present).toBe(5);
    expect(h.past).toEqual([]);
  });

  it("merges quick repeats of the same control into one step", () => {
    let h = H.createHistory(0);
    h = H.push(h, 1, { coalesce: "gain:t1", now: 1000 });
    h = H.push(h, 2, { coalesce: "gain:t1", now: 1100 });
    h = H.push(h, 3, { coalesce: "gain:t1", now: 1200 });
    expect(h.past).toEqual([0]);
    h = H.push(h, 4, { coalesce: "gain:t1", now: 5000 }); // too late: a new step
    h = H.push(h, 5, { coalesce: "pan:t1", now: 5010 }); // another control: a new step
    expect(h.past).toEqual([0, 3, 4]);
  });

  it("ignores a push that changes nothing", () => {
    const h = H.push(H.createHistory("a"), "b");
    expect(H.push(h, "b")).toBe(h);
  });
});
