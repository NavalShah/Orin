"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Card, type Job } from "@/lib/api";
import { pct, stageLabel } from "@/lib/format";
import { handoff } from "@/lib/handoff";
import css from "./Chat.module.css";

type Phase = "running" | "done" | "needs_choice" | "failed" | "unknown" | "stored";

/** A transcription started from the chat: progress while its job runs, then a link to the Studio. */
export function ConversionCard({ card }: { card: Card }) {
  const [job, setJob] = useState<Job | null>(null);
  const [phase, setPhase] = useState<Phase>(card.job_id ? "running" : "stored");
  const [conversionId, setConversionId] = useState(card.conversion_id);

  useEffect(() => {
    const jobId = card.job_id;
    if (!jobId) return;
    setPhase("running");
    const ac = new AbortController();
    api
      .watchJob(jobId, setJob, ac.signal)
      .then((final) => {
        setJob(final);
        if (final.result?.kind === "conversion") setConversionId(final.result.id);
        if (final.status === "done") setPhase("done");
        else if (final.status === "awaiting_input") setPhase("needs_choice");
        else setPhase("failed");
      })
      .catch((err) => {
        // An old job the server no longer tracks: the conversion itself may still be there.
        if ((err as Error)?.name !== "AbortError") setPhase("unknown");
      });
    return () => ac.abort();
  }, [card.job_id]);

  const link = conversionId ? (
    <Link className={`o-btn o-btn-small ${css.linkButton}`} href={handoff.studioWithConversion(conversionId)}>
      Open in the Notation Studio
    </Link>
  ) : null;

  return (
    <section className={css.card} aria-label="Transcription">
      <h3 className={css.cardTitle}>Transcription</h3>
      {card.text ? <p>{card.text}</p> : null}
      {phase === "running" ? (
        <div className="o-row" style={{ flexWrap: "nowrap" }}>
          <progress className="o-progress" max={1} {...(job ? { value: Math.min(1, Math.max(0, job.progress)) } : {})} aria-label="Transcription progress" />
          <span className="o-mono" aria-hidden="true">
            {job ? pct(job.progress) : ""}
          </span>
        </div>
      ) : null}
      <p role="status" aria-live="polite">
        {phase === "running"
          ? `${stageLabel(job?.stage)}...`
          : phase === "done"
            ? "The notation is ready."
            : phase === "needs_choice"
              ? "Orin is not sure of the style. Open the Notation Studio to choose one."
              : phase === "failed"
                ? `The transcription did not finish. ${job?.error?.message ?? ""}`
                : phase === "stored"
                  ? "This transcription was started from the chat. Open the Notation Studio to see its result."
                  : "The progress of this transcription is no longer tracked. It may still have finished."}
      </p>
      {phase === "running" ? (
        <p className="o-muted">You can keep chatting while this runs.</p>
      ) : (
        <div className="o-row">{link ?? <span className="o-muted">No conversion was recorded for this card.</span>}</div>
      )}
    </section>
  );
}
