"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import type { Level, UserPrefs } from "@/lib/api";
import { GlossaryDialog, GlossaryProvider } from "./Glossary";
import { LibraryProvider, useLibrary } from "./Library";
import { RegistryProvider, useRegistry } from "./registry";
import { SessionGate, useSession } from "./session";
import css from "./Shell.module.css";

const AREAS = [
  { href: "/chat", label: "Chat" },
  { href: "/studio", label: "Notation Studio" },
  { href: "/workspace", label: "Workspace" },
];

export const LEVELS: Array<{ value: Level; label: string }> = [
  { value: "beginner", label: "Beginner" },
  { value: "intermediate", label: "Intermediate" },
  { value: "advanced", label: "Advanced" },
];

const NOTE_NAMES: Array<{ value: UserPrefs["labels"]; label: string }> = [
  { value: "tradition", label: "Each tradition's own names" },
  { value: "solfege", label: "Do Re Mi" },
  { value: "letters", label: "Letters (C D E)" },
];

/** The shared shell for /chat, /studio and /workspace (spec 3.4). */
export function Shell({ children }: { children: ReactNode }) {
  return (
    <>
      <a className={css.skip} href="#orin-main">
        Skip to content
      </a>
      <RegistryProvider>
        <GlossaryProvider>
          <SessionGate>
            <LibraryProvider>
              <Frame>{children}</Frame>
            </LibraryProvider>
          </SessionGate>
        </GlossaryProvider>
      </RegistryProvider>
    </>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "";
  const { user, prefs, updatePrefs, signOut, prefsError } = useSession();
  const library = useLibrary();
  const [glossaryOpen, setGlossaryOpen] = useState(false);

  return (
    <div className={css.frame}>
      <div className={css.nav}>
        <p className={css.brand}>
          <span className={css.brandMark} aria-hidden="true">
            O
          </span>
          Orin
        </p>
        <nav aria-label="Orin areas">
          <ul className={css.navList}>
            {AREAS.map((area) => (
              <li key={area.href}>
                <Link
                  href={area.href}
                  className={css.navLink}
                  aria-current={pathname === area.href || pathname.startsWith(`${area.href}/`) ? "page" : undefined}
                >
                  {area.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className={css.navTools}>
          <button type="button" className="o-btn" onClick={library.open}>
            Library
          </button>
          <button type="button" className="o-btn" onClick={() => setGlossaryOpen(true)}>
            Glossary
          </button>
        </div>
        <div className={css.prefs}>
          <label className="o-label">
            Explanation level
            <select
              className="o-select"
              value={prefs.level}
              onChange={(e) => void updatePrefs({ level: e.target.value as Level })}
            >
              {LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <label className="o-label">
            Note names
            <select
              className="o-select"
              value={prefs.labels}
              onChange={(e) => void updatePrefs({ labels: e.target.value as UserPrefs["labels"] })}
            >
              {NOTE_NAMES.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          {prefsError ? (
            <p className="o-alert" role="alert">
              {prefsError}
            </p>
          ) : null}
        </div>
        <div className={css.account}>
          <p>
            Signed in as <strong>{user.name}</strong>
            <span className="o-muted"> (demo profile, no password)</span>
          </p>
          <button type="button" className="o-btn o-btn-small" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
        <Link href="/" className={css.backLink}>
          Back to the original sketch
        </Link>
      </div>
      <div className={css.content}>
        <StatusStrip />
        <main id="orin-main" tabIndex={-1} className={css.main}>
          {children}
        </main>
      </div>
      {glossaryOpen && <GlossaryDialog onClose={() => setGlossaryOpen(false)} />}
    </div>
  );
}

/** Says, in words, which services are running on their offline stand-ins (spec D11). */
export function StatusStrip() {
  const { registry } = useRegistry();
  const off = registry.offline;
  const lines: string[] = [];
  if (off.llm) lines.push("Gemini is on its offline stand-in: chat replies and explanations come from Orin's built-in text.");
  if (off.music) lines.push("Music generation is on its offline stand-in: new tracks are simple synthesised sketches, not real generations.");
  if (off.tts) lines.push("Narration is on its offline stand-in: explanations are read by this browser's own voice.");
  if (lines.length === 0) return null;
  const parts = [off.llm && "chat and explanations", off.music && "music generation", off.tts && "narration"].filter(Boolean);
  // One line by default so it does not push the screen down; the details stay in the
  // page (and in the accessibility tree) and open on request.
  return (
    <aside className={css.strip} aria-label="Service status">
      <details>
        <summary>
          <strong>Offline stand-ins in use</strong> for {parts.join(", ")}. Results from them are labelled.
        </summary>
        <ul>
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {off.reason ? <p className="o-muted">Why: {off.reason}</p> : null}
      </details>
    </aside>
  );
}
