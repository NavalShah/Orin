"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import css from "./Shell.module.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, audio[controls], [tabindex]:not([tabindex="-1"])';

/** A modal dialog: focus moves in, stays in, and returns on close; Escape closes it (spec 13.5). */
export function Dialog({
  title,
  onClose,
  children,
  variant = "center",
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  variant?: "center" | "drawer";
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.focus();

    // Keys are handled on the document, so they still work if the focused control was
    // removed (a deleted row, say) and focus fell back to the page.
    function onKeyDown(e: KeyboardEvent) {
      const root = ref.current;
      if (!root) return;
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.closest("[hidden]"));
      if (items.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const outside = !active || !root.contains(active);
      if (e.shiftKey && (outside || active === first || active === root)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (outside || active === last)) {
        e.preventDefault();
        first.focus();
      }
    }
    // If focus ever lands outside (for example past a collapsed section), bring it back.
    function onFocusIn(e: FocusEvent) {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) ref.current.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("focusin", onFocusIn);
      previous?.focus?.();
    };
  }, []);

  return (
    <div
      className={css.backdrop}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={variant === "drawer" ? css.drawer : css.dialog}
      >
        <div className={css.dialogHead}>
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="o-btn o-btn-small" onClick={onClose}>
            Close
          </button>
        </div>
        <div className={css.dialogBody}>{children}</div>
      </div>
    </div>
  );
}
