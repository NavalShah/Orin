"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, errorText, type Asset, type Card, type ChatEvent, type Message, type MessageCreate, type Thread,
} from "@/lib/api";
import { readHandoff } from "@/lib/handoff";
import { useLibrary } from "@/components/shell/Library";
import { useRegistry } from "@/components/shell/registry";
import { AudioCard } from "./AudioCard";
import { Composer } from "./Composer";
import { ConversionCard } from "./ConversionCard";
import { SpecCard } from "./SpecCard";
import { ThreadList } from "./ThreadList";
import css from "./Chat.module.css";

type Live = { threadId: string; text: string; intent: ChatEvent["intent"]; cards: Card[]; settled: boolean };
type Problem = { text: string; retry?: () => void };

const EXPLAIN_TRACK = "Please explain this music: what is going on in it, and what should I listen for?";
const ASK_REPORT = "Can you explain this loss report? What was lost in the conversion, and why?";
const ASK_ITEMS = "Can you explain the items I picked from this loss report? What was lost, and why?";
const STARTERS = [
  "What can you help me with?",
  "What is a loss report, and how do I read one?",
  "Make a calm 30-second piece I can transcribe.",
];

const isAbort = (err: unknown) => (err as Error)?.name === "AbortError";

function intentLine(intent: ChatEvent["intent"]): string {
  if (intent === "generate") return "Planning a track...";
  if (intent === "transcribe" || intent === "convert") return "Setting up the transcription...";
  if (intent === "clarify") return "Orin has a question...";
  return "Orin is thinking...";
}

/** The Chat (spec 10): threads, a streaming reply, and cards for specs, tracks and conversions. */
export function ChatScreen() {
  const { registry } = useRegistry();
  const library = useLibrary();
  const refreshLibrary = library.refresh;
  const router = useRouter();
  const params = useSearchParams();
  const incoming = useMemo(() => readHandoff(params), [params]);

  const [threads, setThreads] = useState<Thread[]>([]);
  const [loadingThreads, setLoadingThreads] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [notice, setNotice] = useState("");
  const [attachments, setAttachments] = useState<Asset[]>([]);
  const [genreId, setGenreId] = useState<string | null>(null);
  // conversion id -> job id, for conversion cards whose job id only arrived in a "job" event
  const [jobs, setJobs] = useState<Record<string, string>>({});

  const activeRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const handledRef = useRef<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const streaming = live !== null;

  const activate = useCallback((id: string | null) => {
    activeRef.current = id;
    setActiveId(id);
  }, []);

  const loadMessages = useCallback(async (id: string) => {
    try {
      const list = await api.messages(id);
      if (activeRef.current === id) setMessages(list);
    } catch (err) {
      if (activeRef.current === id) setProblem({ text: `The messages could not be loaded. ${errorText(err)}`, retry: () => void loadMessages(id) });
    }
  }, []);

  const loadThreads = useCallback(async (selectFirst: boolean) => {
    try {
      const list = await api.threads();
      // Keep any thread created here while the list was loading.
      setThreads((mine) => [...mine.filter((t) => !list.some((s) => s.id === t.id)), ...list]);
      if (selectFirst && !activeRef.current && list[0]) {
        activate(list[0].id);
        void loadMessages(list[0].id);
      }
    } catch (err) {
      setProblem({ text: `Your conversations could not be loaded. ${errorText(err)}`, retry: () => void loadThreads(selectFirst) });
    } finally {
      setLoadingThreads(false);
    }
  }, [activate, loadMessages]);

  const send = useCallback(
    async (body: MessageCreate, threadId?: string | null) => {
      setProblem(null);
      setNotice("");
      let id = threadId ?? activeRef.current;
      if (!id) {
        try {
          const created = await api.createThread();
          setThreads((list) => [created, ...list]);
          setMessages([]);
          activate(created.id);
          id = created.id;
        } catch (err) {
          setProblem({ text: `A new conversation could not be started. ${errorText(err)}`, retry: () => void send(body, threadId) });
          return;
        }
      }
      const tid = id;
      const pending: Message = {
        id: `pending-${Date.now()}`,
        thread_id: tid,
        role: "user",
        text: body.text,
        cards: [],
        track_ids: body.attachment_asset_ids ?? [],
        spec: null,
        created_at: new Date().toISOString(),
        meta: {},
      };
      setMessages((list) => [...list, pending]);
      const ac = new AbortController();
      abortRef.current = ac;
      setLive({ threadId: tid, text: "", intent: null, cards: [], settled: false });
      let lastJob: string | null = null;
      let lastConversion: string | null = null;
      const link = () => {
        const conversionId = lastConversion;
        const jobId = lastJob;
        if (conversionId && jobId) setJobs((map) => ({ ...map, [conversionId]: jobId }));
      };
      try {
        for await (const ev of api.sendMessage(tid, body, ac.signal)) {
          if (ev.type === "intent") {
            setLive((l) => (l ? { ...l, intent: ev.intent } : l));
          } else if (ev.type === "delta") {
            const piece = ev.text ?? "";
            setLive((l) => (l ? { ...l, text: l.text + piece } : l));
          } else if (ev.type === "card") {
            const card = ev.card;
            if (card) setLive((l) => (l ? { ...l, cards: [...l.cards, card] } : l));
            if (card?.kind === "conversion" && card.conversion_id && !card.job_id) {
              lastConversion = card.conversion_id;
              link();
            }
          } else if (ev.type === "job") {
            lastJob = ev.job_id;
            link();
          } else if (ev.type === "message") {
            // The stored assistant message replaces the streamed draft.
            const stored = ev.message;
            if (stored) {
              setMessages((list) => [...list, stored]);
              setLive((l) => (l ? { ...l, text: "", cards: [], settled: true } : l));
            }
          } else if (ev.type === "error") {
            const e = ev.error;
            setProblem({
              text: e ? (e.hint ? `${e.message} ${e.hint}` : e.message) : "Orin could not answer.",
              retry: () => void send(body, tid),
            });
            break;
          } else if (ev.type === "done") {
            break;
          }
        }
      } catch (err) {
        if (isAbort(err)) {
          if (activeRef.current === tid) setNotice("Stopped. The reply was cut short.");
        } else setProblem({ text: errorText(err), retry: () => void send(body, tid) });
      } finally {
        // A newer send may already own the live reply (after switching threads mid-stream).
        if (abortRef.current === ac) {
          abortRef.current = null;
          setLive(null);
        }
        // Swap the optimistic copy for what the server stored; titles may have changed too.
        if (activeRef.current === tid) await loadMessages(tid);
        void loadThreads(false);
      }
    },
    [activate, loadMessages, loadThreads],
  );

  // First load. A loss-report handoff opens its own new thread instead of the latest one.
  useEffect(() => {
    void loadThreads(!incoming.conversion);
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Handoff: /chat?conversion=ID[&items=..] starts a thread that asks about that loss report.
  useEffect(() => {
    const conversionId = incoming.conversion;
    if (!conversionId) return;
    const key = `${conversionId}:${incoming.items.join(",")}`;
    if (handledRef.current === key) return;
    handledRef.current = key;
    (async () => {
      try {
        const created = await api.createThread("About a loss report");
        setThreads((list) => [created, ...list.filter((t) => t.id !== created.id)]);
        setMessages([]);
        activate(created.id);
        router.replace("/chat", { scroll: false });
        await send(
          { text: incoming.items.length > 0 ? ASK_ITEMS : ASK_REPORT, context: { conversion_id: conversionId, item_indexes: incoming.items } },
          created.id,
        );
      } catch (err) {
        handledRef.current = null;
        setProblem({ text: `The loss report could not be brought into the chat. ${errorText(err)}` });
      }
    })();
  }, [incoming, activate, router, send]);

  // Handoff: /chat?asset=ID attaches that track to the next message.
  useEffect(() => {
    const assetId = incoming.asset;
    if (!assetId) return;
    let alive = true;
    api
      .getAsset(assetId)
      .then((asset) => {
        if (!alive) return;
        setAttachments((list) => (list.some((a) => a.id === asset.id) ? list : [...list, asset]));
        setNotice(`${asset.filename} is attached to your next message.`);
      })
      .catch((err) => {
        if (alive) setProblem({ text: `The track from the link could not be attached. ${errorText(err)}` });
      });
    return () => {
      alive = false;
    };
  }, [incoming.asset]);

  // Keep the newest text in view.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, live]);

  function select(id: string) {
    if (id === activeRef.current) return;
    abortRef.current?.abort();
    setProblem(null);
    setNotice("");
    setMessages([]);
    activate(id);
    void loadMessages(id);
  }

  function newChat() {
    abortRef.current?.abort();
    setProblem(null);
    setNotice("New conversation. Your first message will start it.");
    setMessages([]);
    activate(null);
  }

  async function rename(id: string, title: string) {
    try {
      const updated = await api.renameThread(id, title);
      setThreads((list) => list.map((t) => (t.id === id ? updated : t)));
    } catch (err) {
      setProblem({ text: `The conversation could not be renamed. ${errorText(err)}`, retry: () => void rename(id, title) });
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteThread(id);
      setThreads((list) => list.filter((t) => t.id !== id));
      if (activeRef.current === id) {
        abortRef.current?.abort();
        setMessages([]);
        activate(null);
      }
      setNotice("The conversation was deleted.");
    } catch (err) {
      setProblem({ text: `The conversation could not be deleted. ${errorText(err)}`, retry: () => void remove(id) });
    }
  }

  function sendText(text: string) {
    const body: MessageCreate = { text };
    if (attachments.length > 0) body.attachment_asset_ids = attachments.map((a) => a.id);
    if (genreId) body.genre_id = genreId;
    setAttachments([]);
    setGenreId(null);
    void send(body);
  }

  function onGenerated() {
    const id = activeRef.current;
    if (id) void loadMessages(id);
    void refreshLibrary();
  }

  function renderCard(card: Card, key: string, threadId: string) {
    if (card.kind === "spec") return <SpecCard key={key} card={card} threadId={threadId} registry={registry} onDone={onGenerated} />;
    if (card.kind === "audio") {
      return (
        <AudioCard
          key={key}
          card={card}
          busy={streaming}
          onExplain={(asset) => void send({ text: EXPLAIN_TRACK, attachment_asset_ids: [asset.id] }, threadId)}
        />
      );
    }
    if (card.kind === "conversion") {
      const jobId = card.job_id ?? (card.conversion_id ? jobs[card.conversion_id] : undefined);
      return <ConversionCard key={key} card={jobId ? { ...card, job_id: jobId } : card} />;
    }
    return (
      <p key={key} className="o-note">
        {card.text ?? (card.notes ?? []).join(" ")}
      </p>
    );
  }

  const active = threads.find((t) => t.id === activeId) ?? null;
  const assetName = (id: string) => library.assets.find((a) => a.id === id)?.filename ?? "a track";

  return (
    <div className={css.chat}>
      <ThreadList
        threads={threads}
        activeId={activeId}
        loading={loadingThreads}
        onSelect={select}
        onCreate={newChat}
        onRename={(id, title) => void rename(id, title)}
        onDelete={(id) => void remove(id)}
      />

      <div className={css.conversation}>
        <header className="o-stack" style={{ gap: 2 }}>
          <h1>Chat</h1>
          <p className="o-muted">{active ? active.title || "Untitled chat" : "New conversation"}</p>
        </header>

        <div ref={listRef} className={css.messages} tabIndex={0} role="group" aria-label="Messages">
          {messages.length === 0 && !streaming ? (
            <div className="o-stack">
              <p>
                Ask Orin about music, ask for a short track in a style, or attach a track and ask for its notation. Orin shows a
                plan before it generates anything.
              </p>
              <div className="o-row">
                {STARTERS.map((s) => (
                  <button key={s} type="button" className="o-btn o-btn-small" onClick={() => sendText(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          <ol className={css.messageList}>
            {messages.map((m) => (
              <li key={m.id} className={m.role === "user" ? css.fromUser : css.fromOrin}>
                <p className={css.who}>{m.role === "user" ? "You" : "Orin"}</p>
                {m.text ? <p className={css.text}>{m.text}</p> : null}
                {m.role === "user" && (m.track_ids ?? []).length > 0 ? (
                  <p className="o-muted">Attached: {(m.track_ids ?? []).map(assetName).join(", ")}</p>
                ) : null}
                {(m.cards ?? []).map((card, i) => renderCard(card, `${m.id}-${i}`, m.thread_id))}
              </li>
            ))}
          </ol>

          {/* The reply as it arrives. This region exists before any text does, so each piece is announced. */}
          <div aria-live="polite" aria-busy={streaming} className={css.live}>
            {live && !live.settled ? (
              <div className={css.fromOrin}>
                <p className={css.who}>Orin</p>
                {live.text ? <p className={css.text}>{live.text}</p> : live.cards.length === 0 ? <p className="o-muted">{intentLine(live.intent)}</p> : null}
                {live.cards.map((card, i) => renderCard(card, `live-${i}`, live.threadId))}
              </div>
            ) : null}
          </div>
        </div>

        <p role="status" aria-live="polite" className={notice ? "o-muted" : "o-sr-only"}>
          {notice}
        </p>
        {problem ? (
          <div className="o-alert" role="alert">
            <p>{problem.text}</p>
            <p className="o-row" style={{ marginTop: 8 }}>
              {problem.retry ? (
                <button type="button" className="o-btn o-btn-small" onClick={problem.retry} disabled={streaming}>
                  Retry
                </button>
              ) : null}
              <button type="button" className="o-btn o-btn-small" onClick={() => setProblem(null)}>
                Dismiss
              </button>
            </p>
          </div>
        ) : null}

        <Composer
          registry={registry}
          streaming={streaming}
          attachments={attachments}
          onAttach={(asset) => setAttachments((list) => (list.some((a) => a.id === asset.id) ? list : [...list, asset]))}
          onDetach={(id) => setAttachments((list) => list.filter((a) => a.id !== id))}
          genreId={genreId}
          onGenre={setGenreId}
          onSend={sendText}
          onStop={() => abortRef.current?.abort()}
        />
      </div>
    </div>
  );
}
