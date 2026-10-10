"use client";

import { useState, type FormEvent } from "react";
import type { Thread } from "@/lib/api";
import { useFocusAfter } from "@/components/shell/useFocusAfter";
import css from "./Chat.module.css";

/** The user's conversations: create, select, rename, delete. */
export function ThreadList({
  threads,
  activeId,
  loading,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  threads: Thread[];
  activeId: string | null;
  loading: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const focusAfter = useFocusAfter();

  function endRename(id: string) {
    focusAfter(`orin-thread-${id}`);
    setRenaming(null);
  }

  function submit(e: FormEvent, id: string) {
    e.preventDefault();
    const next = title.trim();
    if (next) onRename(id, next);
    endRename(id);
  }

  return (
    <nav className={`o-panel ${css.threads}`} aria-label="Conversations">
      <div className="o-row" style={{ justifyContent: "space-between" }}>
        <h2>Conversations</h2>
        <button type="button" id="orin-new-chat" className="o-btn o-btn-small" onClick={onCreate}>
          New chat
        </button>
      </div>
      {loading ? <p role="status">Loading conversations...</p> : null}
      {!loading && threads.length === 0 ? <p className="o-muted">No conversations yet. Send a message to start one.</p> : null}
      <ul className={css.threadList}>
        {threads.map((t) => (
          <li key={t.id} className={css.thread} data-active={t.id === activeId ? "true" : undefined}>
            {renaming === t.id ? (
              <form className="o-stack" style={{ gap: 6 }} onSubmit={(e) => submit(e, t.id)}>
                <label className="o-label">
                  New name
                  <input
                    className="o-input"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    maxLength={120}
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === "Escape") endRename(t.id);
                    }}
                  />
                </label>
                <div className="o-row" style={{ gap: 6 }}>
                  <button type="submit" className="o-btn o-btn-small">
                    Save
                  </button>
                  <button type="button" className="o-btn o-btn-small" onClick={() => endRename(t.id)}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <>
                <button
                  type="button"
                  id={`orin-thread-${t.id}`}
                  className={css.threadButton}
                  aria-current={t.id === activeId ? "true" : undefined}
                  onClick={() => onSelect(t.id)}
                >
                  {t.title || "Untitled chat"}
                </button>
                <div className="o-row" style={{ gap: 6 }}>
                  <button
                    type="button"
                    className="o-btn o-btn-small o-btn-quiet"
                    aria-label={`Rename ${t.title || "untitled chat"}`}
                    onClick={() => {
                      setTitle(t.title);
                      setRenaming(t.id);
                      setConfirming(null);
                    }}
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    id={`orin-thread-del-${t.id}`}
                    className="o-btn o-btn-small o-btn-quiet"
                    aria-label={`Delete ${t.title || "untitled chat"}`}
                    aria-expanded={confirming === t.id}
                    onClick={() => setConfirming(confirming === t.id ? null : t.id)}
                  >
                    Delete
                  </button>
                  {confirming === t.id ? (
                    <>
                      <button
                        type="button"
                        className="o-btn o-btn-small"
                        onClick={() => {
                          focusAfter("orin-new-chat");
                          setConfirming(null);
                          onDelete(t.id);
                        }}
                      >
                        Yes, delete it
                      </button>
                      <button
                        type="button"
                        className="o-btn o-btn-small"
                        onClick={() => {
                          focusAfter(`orin-thread-del-${t.id}`);
                          setConfirming(null);
                        }}
                      >
                        Keep
                      </button>
                    </>
                  ) : null}
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
