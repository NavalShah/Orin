// Notation -> piano roll (spec 12.5, 3.5). A converted score becomes a MIDI clip whose
// notes keep their microtones: each note is the nearest semitone plus a detune in cents.
import type { ConversionOut, FormatScore } from "../api";
import { centsToHz, hzToMidi } from "../audio/synth";
import { uid, type MidiClip, type MidiNote } from "./project";

const round2 = (x: number) => Math.round(x * 100) / 100;

/** A pitch given as cents above a tonic, as a MIDI number (A4 = 440) and what is left over. */
export function pitchFromCents(tonicHz: number, cents: number): { midi: number; detuneCents: number } {
  const exact = hzToMidi(centsToHz(tonicHz, cents));
  const midi = Math.round(exact);
  return { midi, detuneCents: round2((exact - midi) * 100) };
}

export function scoreToNotes(score: Pick<FormatScore, "options" | "notes">): MidiNote[] {
  const tonicHz = score.options.tonic_hz;
  return score.notes
    .filter((n) => Number.isFinite(n.cents) && n.duration_s > 0)
    .map((n) => ({
      id: uid("n"),
      ...pitchFromCents(tonicHz, n.cents),
      startSec: Math.max(0, n.onset_s),
      durSec: n.duration_s,
      velocity: Math.min(1, Math.max(0.05, (n.velocity ?? 80) / 127)),
    }))
    .filter((n) => n.midi >= 0 && n.midi <= 127);
}

/** The score of a conversion as a MIDI clip. Null when the conversion has no notes yet. */
export function conversionToClip(
  conversion: Pick<ConversionOut, "id" | "score" | "output_format"> & { source_asset?: { filename: string } | null },
  at: { trackId: string; startSec: number; id?: string; name?: string },
): MidiClip | null {
  const score = conversion.score;
  if (!score) return null;
  const notes = scoreToNotes(score);
  if (notes.length === 0) return null;
  const end = notes.reduce((m, n) => Math.max(m, n.startSec + n.durSec), 0);
  const source = conversion.source_asset?.filename?.replace(/\.[^.]+$/, "");
  return {
    id: at.id ?? uid("clip"),
    trackId: at.trackId,
    kind: "midi",
    name: at.name ?? (source ? `${source} (${conversion.output_format})` : `Notation (${conversion.output_format})`),
    startSec: Math.max(0, at.startSec),
    lengthSec: Math.max(0.25, end),
    tonicHz: score.options.tonic_hz,
    notes,
    conversionId: conversion.id,
  };
}
