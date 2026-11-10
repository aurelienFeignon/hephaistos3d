"use client";

import { useCallback, useState } from "react";
import { createApiClient } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { GenerationDetail } from "./GenerationDetail";
import { GenerationPanel } from "./GenerationPanel";
import { Library } from "./Library";
import { QueueBar, queuePollDelay } from "./QueueBar";
import { ResultViewer } from "./ResultViewer";
import { StudioShell } from "./StudioShell";
import { SystemBar } from "./SystemBar";

const api = createApiClient();

const loadQueue = (signal: AbortSignal) => api.queue({ limit: 50 }, signal);

export function Studio() {
  const { data, failed, refresh } = usePolling(loadQueue, queuePollDelay);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const refreshAll = useCallback(() => {
    refresh();
    setRefreshKey((key) => key + 1);
  }, [refresh]);
  const cancelFromQueue = useCallback(
    async (id: string) => {
      await api.cancel(id);
      refreshAll();
    },
    [refreshAll],
  );
  return (
    <StudioShell
      status={<SystemBar refreshKey={refreshKey} onDispatchChanged={refreshAll} />}
      library={<Library selectedId={selectedId} onSelect={setSelectedId} hasActive={Boolean(data?.items.length)} refreshKey={refreshKey} />}
      viewer={<ResultViewer id={selectedId} />}
      panelTitle={selectedId ? "Détail de la génération" : "Nouvelle génération"}
      panelOnMobile={selectedId !== null}
      revealPanelFor={selectedId}
      panel={
        <>
          {/* Le formulaire reste monté : consulter une génération ne perd ni l'image ni les réglages. */}
          <div className={selectedId ? "hidden" : "flex flex-1 flex-col"}>
            <GenerationPanel onCreated={refreshAll} refreshKey={refreshKey} />
          </div>
          {selectedId && (
            <GenerationDetail
              key={selectedId}
              id={selectedId}
              onClose={() => setSelectedId(null)}
              onChanged={refreshAll}
              onDeleted={() => {
                setSelectedId(null);
                refreshAll();
              }}
            />
          )}
        </>
      }
      queue={<QueueBar items={data?.items ?? null} failed={failed} onCancel={cancelFromQueue} />}
    />
  );
}
