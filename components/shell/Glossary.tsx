"use client";

import { createContext, useContext, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { api, errorText, type GlossaryEntry } from "@/lib/api";
import { Dialog } from "./Dialog";
import css from "./Shell.module.css";

type GlossaryValue = {
  entries: GlossaryEntry[];
  error: string | null;
  lookup: (term: string) => GlossaryEntry | undefined;
};

const empty: GlossaryValue = { entries: [], error: null, lookup: () => undefined };
const GlossaryContext = createContext<GlossaryValue>(empty);

export const useGlossary = (): GlossaryValue => useContext(GlossaryContext);

export function GlossaryProvider({ children, entries: given }: { children: ReactNode; entries?: GlossaryEntry[] }) {
  const [entries, setEntries] = useState<GlossaryEntry[]>(given ?? []);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (given) return;
    let live = true;
    api
      .glossary()
      .then((data) => {
        if (live) setEntries(data);
      })
      .catch((err) => {
        if (live) setError(errorText(err));
      });
    return () => {
      live = false;
    };
  }, [given]);

  const value = useMemo<GlossaryValue>(() => {
    const byTerm = new Map(entries.map((e) => [e.term.toLowerCase(), e]));
    return { entries, error, lookup: (term) => byTerm.get(term.trim().toLowerCase()) };
  }, [entries, error]);

  return <GlossaryContext.Provider value={value}>{children}</GlossaryContext.Provider>;
}

/**
 * A word with a glossary definition (spec 11.4). The definition shows on hover and on focus,
 * is tied to the word with aria-describedby, and Escape hides it. Words the glossary does not
 * know render as plain text.
 */
export function Term({ name, children }: { name: string; children?: ReactNode }) {
  const { lookup } = useGlossary();
  const entry = lookup(name);
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!entry) return <>{children ?? name}</>;
  return (
    <span className={css.term} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className={css.termButton}
        aria-describedby={id}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && open) {
            e.stopPropagation();
            setOpen(false);
          }
        }}
      >
        {children ?? name}
      </button>
      <span role="tooltip" id={id} className={css.tooltip} hidden={!open}>
        {entry.definition}
        {entry.tradition ? <span className={css.tooltipTradition}> ({entry.tradition})</span> : null}
      </span>
    </span>
  );
}

export function GlossaryDialog({ onClose }: { onClose: () => void }) {
  const { entries, error } = useGlossary();
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();
  const shown = q
    ? entries.filter((e) => e.term.toLowerCase().includes(q) || e.definition.toLowerCase().includes(q))
    : entries;
  return (
    <Dialog title="Glossary" onClose={onClose}>
      <div className="o-stack">
        <p className="o-muted">
          Short, plain definitions. Traditions use these words in their own ways; treat each line as a starting point.
        </p>
        <label className="o-label">
          Find a word
          <input className="o-input" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
        {error && (
          <p className="o-alert" role="alert">
            The glossary could not be loaded. {error}
          </p>
        )}
        <p className="o-sr-only" role="status">
          {shown.length} {shown.length === 1 ? "word" : "words"} shown
        </p>
        {shown.length === 0 && !error ? <p>No matching words.</p> : null}
        <dl className={css.glossaryList}>
          {shown.map((e) => (
            <div key={`${e.term}-${e.tradition ?? ""}`}>
              <dt>
                {e.term}
                {e.tradition ? <span className="o-muted"> ({e.tradition})</span> : null}
              </dt>
              <dd>{e.definition}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Dialog>
  );
}
