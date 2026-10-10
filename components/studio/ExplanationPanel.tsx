"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, type Explanation, type Level, type LossItem, type Narration } from "@/lib/api";
import { useSession } from "@/components/shell/session";
import { LEVELS } from "@/components/shell/Shell";
import css from "./Studio.module.css";

const PIANO_QUESTION = "Why can't a piano play this?";
const NARRATE_LIMIT = 2500;

/**
 * The explanation of the loss (spec 9.4) and its narration (spec 9.5). It loads after the
 * conversion without holding up the page. Narration never starts by itself; the text on
 * screen is the transcript.
 */
export function ExplanationPanel({
  conversionId,
  revision = 0,
  selectedIndex,
  selectedItem,
}: {
  conversionId: string;
  /** Bump this when the same conversion was re-run, so the explanation is asked for again. */
  revision?: number;
  selectedIndex: number | null;
  selectedItem: LossItem | null;
}) {
  const { prefs, updatePrefs } = useSession();
  const level = prefs.level;
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [question, setQuestion] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);

  const [narration, setNarration] = useState<Narration | null>(null);
  const [narrating, setNarrating] = useState(false);
  const [narrateError, setNarrateError] = useState<string | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  const stopSpeech = useCallback(() => {
    audioRef.current?.pause();
    if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
    setSpeaking(false);
  }, []);

  const ask = useCallback(
    async (itemIndexes: number[], asked: string | null) => {
      const mine = ++request.current;
      stopSpeech();
      setNarration(null);
      setNarrateError(null);
      setLoading(true);
      setError(null);
      setQuestion(asked);
      try {
        const result = await api.explain({
          conversion_id: conversionId,
          level,
          ...(itemIndexes.length ? { item_indexes: itemIndexes } : {}),
          ...(asked ? { question: asked } : {}),
        });
        if (mine === request.current) setExplanation(result);
      } catch (err) {
        if (mine === request.current) setError(errorText(err));
      } finally {
        if (mine === request.current) setLoading(false);
      }
    },
    [conversionId, level, stopSpeech],
  );

  // Ask for the whole report when the conversion, its re-run or the level changes.
  useEffect(() => {
    void ask([], null);
  }, [ask, revision]);

  useEffect(() => stopSpeech, [stopSpeech]);

  async function narrate() {
    if (!explanation) return;
    setNarrating(true);
    setNarrateError(null);
    try {
      const text = [explanation.text, ...explanation.try_this.map((t) => `Try this: ${t}`)].join(" ").slice(0, NARRATE_LIMIT);
      setNarration(await api.narrate(text));
    } catch (err) {
      setNarrateError(errorText(err));
    } finally {
      setNarrating(false);
    }
  }

  function toggleSpeech() {
    if (!narration) return;
    if (!narration.standin && narration.asset) {
      const audio = audioRef.current;
      if (!audio) return;
      if (audio.paused) audio.play().catch(() => setNarrateError("The narration could not be played in this browser."));
      else audio.pause();
      return;
    }
    if (!("speechSynthesis" in window)) {
      setNarrateError("This browser has no built-in voice. The text above is the transcript.");
      return;
    }
    const synth = window.speechSynthesis;
    if (speaking) {
      synth.cancel();
      setSpeaking(false);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(narration.text);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    synth.cancel();
    synth.speak(utterance);
    setSpeaking(true);
  }

  const browserVoice = narration ? narration.standin || !narration.asset : false;

  return (
    <section className="o-panel o-stack" aria-labelledby="orin-explain-title">
      <div className="o-row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
        <h2 id="orin-explain-title">Why was this lost?</h2>
        <label className="o-label">
          Explain for
          <select className="o-select" value={level} onChange={(e) => void updatePrefs({ level: e.target.value as Level })}>
            {LEVELS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="o-row">
        <button
          type="button"
          className="o-btn o-btn-small"
          disabled={loading || selectedIndex == null}
          onClick={() => selectedIndex != null && void ask([selectedIndex], PIANO_QUESTION)}
        >
          {PIANO_QUESTION}
        </button>
        {question ? (
          <button type="button" className="o-btn o-btn-small o-btn-quiet" disabled={loading} onClick={() => void ask([], null)}>
            Back to the whole report
          </button>
        ) : null}
      </div>
      <p className="o-muted">
        {selectedItem
          ? `The question will be asked about the selected item: "${selectedItem.human_text}"`
          : "Select an item in the loss report to ask about that note."}
      </p>

      <div aria-live="polite" aria-busy={loading} className="o-stack">
        {loading ? <p>Writing the explanation...</p> : null}
        {error ? (
          <div className="o-alert" role="alert">
            <p>The explanation could not be loaded. {error}</p>
            <p style={{ marginTop: 8 }}>
              <button type="button" className="o-btn o-btn-small" onClick={() => void ask([], null)}>
                Try again
              </button>
            </p>
          </div>
        ) : null}
        {explanation && !loading && !error ? (
          <>
            {question ? <p className="o-muted">You asked: {question}</p> : null}
            <p className={css.explainText}>{explanation.text}</p>
            {explanation.try_this.length > 0 ? (
              <>
                <h3>Try this</h3>
                <ul className={css.plainList}>
                  {explanation.try_this.map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              </>
            ) : null}
            <p>
              <span className={`o-badge ${explanation.source === "gemini" ? "o-badge-info" : "o-badge-warn"}`}>
                {explanation.source === "gemini" ? "Written by Gemini" : "Offline stand-in: Orin's built-in text"}
              </span>{" "}
              <span className="o-muted">
                {explanation.source === "gemini" ? "from the numbers in the loss report. " : ""}
                {explanation.cached ? "Saved answer." : ""}
              </span>
            </p>
          </>
        ) : null}
      </div>

      {explanation && !loading && !error ? (
        <div className="o-stack" style={{ gap: 8 }}>
          <div className="o-row">
            {!narration ? (
              <button type="button" className="o-btn o-btn-small" disabled={narrating} onClick={() => void narrate()}>
                {narrating ? "Preparing narration..." : "Narrate"}
              </button>
            ) : browserVoice ? (
              <button type="button" className="o-btn o-btn-small" aria-pressed={speaking} onClick={toggleSpeech}>
                <span aria-hidden="true">{speaking ? "■" : "▶"}</span> {speaking ? "Stop reading" : "Play narration"}
              </button>
            ) : (
              <button type="button" className="o-btn o-btn-small" aria-pressed={speaking} onClick={toggleSpeech}>
                <span aria-hidden="true">{speaking ? "❚❚" : "▶"}</span> {speaking ? "Pause narration" : "Play narration"}
              </button>
            )}
          </div>
          {narration && !browserVoice && narration.asset ? (
            <audio
              ref={audioRef}
              src={api.assetUrl(narration.asset)}
              preload="none"
              onPlay={() => setSpeaking(true)}
              onPause={() => setSpeaking(false)}
              onEnded={() => setSpeaking(false)}
            />
          ) : null}
          <p role="status" className="o-muted">
            {narration
              ? browserVoice
                ? `Narration is ready. It will be read by this browser's own voice, a stand-in for Orin's narrator. ${narration.message ?? ""} Nothing plays until you press Play. The text above is the transcript.`
                : "Narration is ready. Nothing plays until you press Play. The text above is the transcript."
              : "Narration reads the explanation aloud. The text above is the transcript."}
          </p>
          {narrateError ? (
            <div className="o-alert" role="alert">
              <p>{narrateError}</p>
              <p style={{ marginTop: 8 }}>
                <button type="button" className="o-btn o-btn-small" onClick={() => void narrate()}>
                  Try again
                </button>
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
