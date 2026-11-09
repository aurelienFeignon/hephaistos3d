"use client";

import { useEffect, useState } from "react";

export type Resource<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error" };

// `load` doit être stable (useCallback) : son changement relance le chargement.
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<Resource<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const settle = (next: Resource<T>) => {
      if (!controller.signal.aborted) setState(next);
    };
    load(controller.signal).then(
      (data) => settle({ status: "ready", data }),
      () => settle({ status: "error" }),
    );
    return () => controller.abort();
  }, [load, attempt]);
  const retry = () => {
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  };
  return { state, retry };
}
