"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import css from "./home.module.css";

type Tradition = { id: string; name: string; line: string; unreviewed: boolean };

// Shown when the Orin server is not running. With the server up, the list comes from its
// registry, so a format added there appears here without touching this file.
const FALLBACK: Tradition[] = [
  { id: "western_classical", name: "Western (staff notation)", line: "Twelve equal pitches per octave, bar lines and key signatures.", unreviewed: true },
  { id: "carnatic", name: "Carnatic (Sangeetham)", line: "South Indian classical: movable Sa, swaras, ragas, talas and gamakas, written as sargam text.", unreviewed: true },
  { id: "western_pop", name: "Western Pop", line: "Twelve equal pitches, 4/4 by default, with syncopated rhythm kept on a sixteenth-note grid.", unreviewed: true },
  { id: "rock", name: "Rock", line: "Twelve equal pitches, riff-based, written as staff plus six-string guitar tab.", unreviewed: true },
  { id: "arabic_maqam", name: "Arabic Maqam", line: "Arabic classical: maqamat with neutral (half-flat) degrees, written on a staff with half-flat signs.", unreviewed: true },
  { id: "persian_dastgah", name: "Persian (Dastgah)", line: "Persian classical: the dastgah system with koron and sori, written on a staff with koron/sori signs.", unreviewed: true },
];

/** The traditions Orin can read and write. Pointing at one shows what it is, and that its tables are a draft. */
export function Traditions() {
  const [items, setItems] = useState<Tradition[]>(FALLBACK);
  const [chosen, setChosen] = useState(FALLBACK[1].id);

  useEffect(() => {
    let alive = true;
    api.formats().then(
      (registry) => {
        if (!alive || registry.formats.length === 0) return;
        setItems(registry.formats.map((f) => ({
          id: f.id, name: f.display_name, line: f.description, unreviewed: f.review_status !== "practitioner_reviewed",
        })));
      },
      () => undefined, // server not running: the built-in list stays
    );
    return () => {
      alive = false;
    };
  }, []);

  const current = items.find((t) => t.id === chosen) ?? items[0];

  return (
    <div className={css.traditions}>
      <div className={css.chips} role="group" aria-label="Traditions">
        {items.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-pressed={t.id === current.id}
            onClick={() => setChosen(t.id)}
            onMouseEnter={() => setChosen(t.id)}
            onFocus={() => setChosen(t.id)}
          >
            {t.name}
          </button>
        ))}
      </div>
      <p className={css.traditionLine} aria-live="polite">
        <strong>{current.name}.</strong> {current.line}{" "}
        {current.unreviewed ? <span className={css.badge}>Unreviewed draft</span> : <span className={css.badge}>Reviewed by a practitioner</span>}
      </p>
    </div>
  );
}
