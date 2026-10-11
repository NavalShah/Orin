"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import css from "./home.module.css";

/**
 * Fades a section in as it scrolls into view. The content is visible by default: it is
 * only held back once the script is running and the browser supports the observer, and
 * the stylesheet turns the effect off for people who ask for reduced motion.
 */
export function Reveal({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"static" | "waiting" | "shown">("static");

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    if (el.getBoundingClientRect().top < window.innerHeight) return; // already on screen: leave it alone
    setState("waiting");
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setState("shown");
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -10% 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className={`${css.reveal} ${state === "waiting" ? css.revealWaiting : ""}`}>
      {children}
    </div>
  );
}
