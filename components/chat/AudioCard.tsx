"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { errorText, type Asset, type Card } from "@/lib/api";
import { clock } from "@/lib/format";
import { handoff } from "@/lib/handoff";
import { Waveform } from "@/components/audio/Waveform";
import { saveAsset } from "@/components/shell/download";
import css from "./Chat.module.css";

/**
 * A track in the chat (spec 10.11): waveform, play, download, and the handoffs to the
 * Workspace and the Notation Studio. A stand-in says so in words, with the reason.
 */
export function AudioCard({
  card,
  onExplain,
  busy = false,
}: {
  card: Card;
  onExplain?: (asset: Asset) => void;
  busy?: boolean;
}) {
  const titleId = useId();
  const [error, setError] = useState<string | null>(null);
  const asset = card.asset;
  if (!asset) {
    return <p className="o-note">{card.text ?? "This track is no longer available."}</p>;
  }
  const standin = card.standin || asset.standin;

  async function download(a: Asset) {
    setError(null);
    try {
      await saveAsset(a);
    } catch (err) {
      setError(`The download failed. ${errorText(err)}`);
    }
  }

  return (
    <section className={css.card} aria-labelledby={titleId}>
      <h3 id={titleId} className={css.cardTitle}>
        {asset.filename}
        {asset.duration_s != null ? <span className="o-muted o-mono"> {clock(asset.duration_s)}</span> : null}
      </h3>
      {card.text ? <p>{card.text}</p> : null}
      {standin ? (
        <p className={css.standin}>
          <span className="o-badge o-badge-warn">Offline stand-in</span> This is a simple synthesised sketch made inside Orin, not a
          track from the music generator.{card.standin_reason ? ` Why: ${card.standin_reason}` : ""}
        </p>
      ) : null}
      <Waveform asset={asset} />
      <div className="o-row">
        <button type="button" className="o-btn o-btn-small" onClick={() => void download(asset)} aria-label={`Download ${asset.filename}`}>
          Download
        </button>
        <Link className={`o-btn o-btn-small ${css.linkButton}`} href={handoff.workspaceWithAsset(asset.id)}>
          Open in Workspace
        </Link>
        <Link className={`o-btn o-btn-small ${css.linkButton}`} href={handoff.studioWithAsset(asset.id)}>
          Transcribe
        </Link>
        <button type="button" className="o-btn o-btn-small" onClick={() => onExplain?.(asset)} disabled={busy || !onExplain}>
          Explain
        </button>
      </div>
      {card.provider && !standin ? (
        <p className="o-muted">
          Made with {card.provider.name}
          {card.provider.model ? ` (${card.provider.model})` : ""}.
        </p>
      ) : null}
      {error ? (
        <p className="o-alert" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
