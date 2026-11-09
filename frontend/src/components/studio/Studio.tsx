"use client";

import { useState, type ReactNode } from "react";
import { createApiClient } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { GenerationDetail } from "./GenerationDetail";
import { GenerationPanel } from "./GenerationPanel";
import { Library } from "./Library";
import { QueueBar, queuePollDelay } from "./QueueBar";
import { StudioShell } from "./StudioShell";

const api = createApiClient();

const loadQueue = (signal: AbortSignal) => api.queue({ limit: 50 }, signal);

type StudioProps = { status: ReactNode; viewer: ReactNode };

export function Studio({ status, viewer }: StudioProps) {
  const { data, failed, refresh } = usePolling(loadQueue, queuePollDelay);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  return (
    <StudioShell
      status={status}
      library={<Library selectedId={selectedId} onSelect={setSelectedId} hasActive={Boolean(data?.items.length)} />}
      viewer={viewer}
      panelTitle={selectedId ? "Détail de la génération" : "Nouvelle génération"}
      panelOnMobile={selectedId !== null}
      revealPanelFor={selectedId}
      panel={
        <>
          {/* Le formulaire reste monté : consulter une génération ne perd ni l'image ni les réglages. */}
          <div className={selectedId ? "hidden" : "flex flex-1 flex-col"}>
            <GenerationPanel onCreated={refresh} />
          </div>
          {selectedId && <GenerationDetail key={selectedId} id={selectedId} onClose={() => setSelectedId(null)} />}
        </>
      }
      queue={<QueueBar items={data?.items ?? null} failed={failed} />}
    />
  );
}
