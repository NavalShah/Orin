"use client";

import { useState, type KeyboardEvent } from "react";
import type { Asset, FormatsResponse } from "@/lib/api";
import { LibraryPicker } from "@/components/shell/Library";
import css from "./Chat.module.css";

/**
 * The message box (spec 10.11): Enter sends, Shift+Enter makes a new line. Genre chips come
 * from the registry; tracks are attached from the Library. While Orin is replying nothing can
 * be sent (typing still works, so focus is not lost) and a Stop button ends the reply.
 */
export function Composer({
  registry,
  streaming,
  attachments,
  onAttach,
  onDetach,
  genreId,
  onGenre,
  onSend,
  onStop,
}: {
  registry: FormatsResponse;
  streaming: boolean;
  attachments: Asset[];
  onAttach: (asset: Asset) => void;
  onDetach: (id: string) => void;
  genreId: string | null;
  onGenre: (id: string | null) => void;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const canSend = !streaming && text.trim().length > 0;

  function submit() {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form
      className={`o-panel o-stack ${css.composer}`}
      aria-label="Write a message"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="o-row" role="group" aria-label="Genre for this message (optional)" style={{ gap: 6 }}>
        <span className="o-muted" aria-hidden="true">
          Genre
        </span>
        {registry.formats.map((f) => (
          <button
            key={f.id}
            type="button"
            className={`o-btn o-btn-small ${css.chip}`}
            aria-pressed={genreId === f.id}
            onClick={() => onGenre(genreId === f.id ? null : f.id)}
            disabled={streaming}
          >
            {f.display_name}
          </button>
        ))}
      </div>

      <details className={css.attach} open={attachments.length > 0 ? true : undefined}>
        <summary>Attach a track from your Library{attachments.length > 0 ? ` (${attachments.length} attached)` : ""}</summary>
        <div className="o-stack" style={{ gap: 8, marginTop: 6 }}>
          <LibraryPicker label="Track" buttonLabel="Attach" onPick={onAttach} disabled={streaming} />
          {attachments.length > 0 ? (
            <ul className={css.attachments} aria-label="Attached tracks">
              {attachments.map((a) => (
                <li key={a.id}>
                  <span>{a.filename}</span>
                  <button
                    type="button"
                    className="o-btn o-btn-small"
                    onClick={() => {
                      // The chip is about to go; put focus in the message box.
                      document.getElementById("orin-composer-box")?.focus();
                      onDetach(a.id);
                    }}
                    aria-label={`Remove ${a.filename}`}
                    disabled={streaming}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </details>

      <label className="o-label">
        Message Orin
        <textarea
          id="orin-composer-box"
          className="o-textarea"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={4000}
          placeholder="Ask about music, ask for a short track, or attach a track and ask for its notation"
          aria-describedby="orin-composer-hint"
        />
      </label>
      <div className="o-row" style={{ justifyContent: "space-between" }}>
        <span id="orin-composer-hint" className="o-muted">
          {streaming ? "Orin is replying. You can type, and send when the reply ends." : "Enter sends. Shift+Enter makes a new line."}
        </span>
        <span className="o-row">
          {streaming ? (
            <button type="button" className="o-btn" onClick={onStop}>
              <span aria-hidden="true">■</span> Stop
            </button>
          ) : null}
          <button type="submit" className="o-btn o-btn-primary" disabled={!canSend}>
            Send
          </button>
        </span>
      </div>
    </form>
  );
}
