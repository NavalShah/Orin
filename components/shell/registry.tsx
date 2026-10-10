"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, errorText, type FormatInfo, type FormatsResponse } from "@/lib/api";

/** The format registry, read once and shared: every dropdown and chip is built from it (spec 4.5). */
export type Registry = {
  registry: FormatsResponse;
  format: (id: string | null | undefined) => FormatInfo | undefined;
  /** Display name for a format id. Unknown ids (a plain genre word, say) come back unchanged. */
  formatName: (id: string | null | undefined) => string;
  reload: () => void;
};

const RegistryContext = createContext<Registry | null>(null);

export function makeRegistry(registry: FormatsResponse, reload: () => void = () => undefined): Registry {
  const byId = new Map(registry.formats.map((f) => [f.id, f]));
  return {
    registry,
    reload,
    format: (id) => (id ? byId.get(id) : undefined),
    formatName: (id) => {
      if (!id) return "Unknown";
      if (id === "auto") return "Auto-detect";
      return byId.get(id)?.display_name ?? id;
    },
  };
}

export function useRegistry(): Registry {
  const value = useContext(RegistryContext);
  if (!value) throw new Error("useRegistry must be used inside the Orin shell.");
  return value;
}

/** Loads the registry. Children only render once it is here; a failure shows the error and a retry. */
export function RegistryProvider({ children, value }: { children: ReactNode; value?: FormatsResponse }) {
  const [registry, setRegistry] = useState<FormatsResponse | null>(value ?? null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    if (value) return;
    let live = true;
    api
      .formats()
      .then((data) => {
        if (!live) return;
        setRegistry(data);
        setError(null);
      })
      .catch((err) => {
        if (live) setError(errorText(err));
      });
    return () => {
      live = false;
    };
  }, [attempt, value]);

  const ctx = useMemo(() => (registry ? makeRegistry(registry, reload) : null), [registry, reload]);

  if (!ctx) {
    return (
      <div className="o-stack" style={{ padding: 24, maxWidth: 560 }}>
        <h1>Orin</h1>
        {error ? (
          <div className="o-alert" role="alert">
            <p>{error}</p>
            <p style={{ marginTop: 8 }}>
              <button
                type="button"
                className="o-btn"
                onClick={() => {
                  setError(null);
                  reload();
                }}
              >
                Try again
              </button>
            </p>
          </div>
        ) : (
          <p role="status">Connecting to the Orin server...</p>
        )}
      </div>
    );
  }
  return <RegistryContext.Provider value={ctx}>{children}</RegistryContext.Provider>;
}
