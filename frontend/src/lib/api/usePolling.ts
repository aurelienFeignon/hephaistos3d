"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// `delayOf(null)` donne le rythme après un échec. `load` et `delayOf` doivent être stables.
export function usePolling<T>(load: (signal: AbortSignal) => Promise<T>, delayOf: (data: T | null) => number) {
  const [state, setState] = useState<{ data: T | null; failed: boolean }>({ data: null, failed: false });
  const pollNow = useRef<() => void>(() => {});

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: AbortController | undefined;

    function stop() {
      clearTimeout(timer);
      inFlight?.abort();
    }

    function poll() {
      stop();
      if (document.hidden) return;
      const controller = new AbortController();
      inFlight = controller;
      load(controller.signal).then(
        (data) => {
          if (controller.signal.aborted) return;
          setState({ data, failed: false });
          timer = setTimeout(poll, delayOf(data));
        },
        () => {
          if (controller.signal.aborted) return;
          setState((current) => ({ ...current, failed: true }));
          timer = setTimeout(poll, delayOf(null));
        },
      );
    }

    pollNow.current = poll;
    poll();
    document.addEventListener("visibilitychange", poll);
    return () => {
      document.removeEventListener("visibilitychange", poll);
      stop();
    };
  }, [load, delayOf]);

  const refresh = useCallback(() => pollNow.current(), []);
  return { ...state, refresh };
}
