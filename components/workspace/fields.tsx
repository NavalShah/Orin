"use client";

import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import s from "./workspace.module.css";

const format = (v: number, digits: number) => String(Math.round(v * 10 ** digits) / 10 ** digits);

type NumberFieldProps = {
  label: string;
  value: number;
  onCommit: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  digits?: number;
  disabled?: boolean;
  hideLabel?: boolean;
  suffix?: ReactNode;
};

/**
 * A number you can type freely: it is applied on Enter or when focus leaves, so a
 * half-typed value never reaches the project. Arrow up and down step it.
 */
export function NumberField({
  label, value, onCommit, min = -Infinity, max = Infinity, step = 1, digits = 2, disabled, hideLabel, suffix,
}: NumberFieldProps) {
  const id = useId();
  const shown = format(value, digits);
  const [text, setText] = useState(shown);
  const [last, setLast] = useState(shown);
  if (shown !== last) {
    setLast(shown);
    setText(shown);
  }

  const commit = (raw: string) => {
    const parsed = Number.parseFloat(raw);
    if (!Number.isFinite(parsed)) {
      setText(shown);
      return;
    }
    const next = Math.min(max, Math.max(min, parsed));
    setText(format(next, digits));
    if (format(next, digits) !== shown) onCommit(next);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commit(text);
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const base = Number.parseFloat(text);
      commit(String((Number.isFinite(base) ? base : value) + (e.key === "ArrowUp" ? step : -step)));
    }
  };

  // The suffix (a note name, a bar number) sits beside the input but is not part of its name.
  return (
    <div className={s.field}>
      <label htmlFor={id} className={hideLabel ? "o-sr-only" : s.fieldLabel}>
        {label}
      </label>
      <span className={s.fieldRow}>
        <input
          id={id}
          className={`o-input ${s.number}`}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={text}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => commit(text)}
          onKeyDown={onKeyDown}
        />
        {suffix ? <span className="o-muted">{suffix}</span> : null}
      </span>
    </div>
  );
}

type TextFieldProps = { label: string; value: string; onCommit: (value: string) => void; hideLabel?: boolean; maxLength?: number };

/** A name field: applied on Enter or when focus leaves; Escape puts the old name back. */
export function TextField({ label, value, onCommit, hideLabel, maxLength = 80 }: TextFieldProps) {
  const [text, setText] = useState(value);
  const [last, setLast] = useState(value);
  if (value !== last) {
    setLast(value);
    setText(value);
  }
  const commit = () => {
    const next = text.trim();
    if (!next) setText(value);
    else if (next !== value) onCommit(next);
  };
  return (
    <label className={s.field}>
      <span className={hideLabel ? "o-sr-only" : s.fieldLabel}>{label}</span>
      <input
        className={`o-input ${s.text}`}
        type="text"
        autoComplete="off"
        maxLength={maxLength}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            setText(value);
          }
        }}
      />
    </label>
  );
}

type SliderProps = {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  max: number;
  step: number;
  hideLabel?: boolean;
  /** What a screen reader says for the value, e.g. "80%" or "20% left". */
  valueText?: string;
  showValue?: boolean;
};

export function Slider({ label, value, onChange, min, max, step, hideLabel, valueText, showValue }: SliderProps) {
  return (
    <label className={s.slider}>
      <span className={hideLabel ? "o-sr-only" : s.fieldLabel}>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-valuetext={valueText}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      {showValue && valueText ? <span className={`o-mono ${s.sliderValue}`} aria-hidden="true">{valueText}</span> : null}
    </label>
  );
}

export const percent = (gain: number) => `${Math.round(gain * 100)}%`;
export const panText = (pan: number) =>
  Math.abs(pan) < 0.005 ? "centre" : `${Math.round(Math.abs(pan) * 100)}% ${pan < 0 ? "left" : "right"}`;
