"use client";

import { useCallback, type ReactNode } from "react";
import { createApiClient } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { GenerationPanel } from "./GenerationPanel";
import { QueueBar, queuePollDelay } from "./QueueBar";
import { StudioShell } from "./StudioShell";

const api = createApiClient();

const loadQueue = (signal: AbortSignal) => api.queue({ limit: 50 }, signal);

type StudioProps = { status: ReactNode; library: ReactNode; viewer: ReactNode };

export function Studio({ status, library, viewer }: StudioProps) {
  const { data, failed, refresh } = usePolling(loadQueue, queuePollDelay);
  const onCreated = useCallback(() => refresh(), [refresh]);
  return (
    <StudioShell
      status={status}
      library={library}
      viewer={viewer}
      panel={<GenerationPanel onCreated={onCreated} />}
      queue={<QueueBar items={data?.items ?? null} failed={failed} />}
    />
  );
}
