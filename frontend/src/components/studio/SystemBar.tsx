"use client";

import { useCallback, useState } from "react";
import { createApiClient } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { ACTIVE_POLL_MS, IDLE_POLL_MS } from "@/lib/generations/format";

const defaultApi = createApiClient();
const GIB = 1024 ** 3;
const gigabytes = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });

export type SystemApi = Pick<typeof defaultApi, "status" | "setDispatch">;
export type SystemStatus = Awaited<ReturnType<SystemApi["status"]>>;
type Worker = SystemStatus["workers"][number];

// Du plus grave au moins grave : avec plusieurs workers, le pire état est celui qu'il faut voir.
const WORKER_STATES: [state: string, label: string, dot: string][] = [
  ["DEGRADED", "Dégradé", "bg-danger"],
  ["NO_GPU", "Sans GPU", "bg-danger"],
  ["WAITING_GPU", "En attente", "bg-accent"],
  ["BUSY", "Génération en cours", "bg-success"],
  ["LOADING", "Chargement du modèle", "bg-success"],
  ["READY", "Modèle chargé", "bg-success"],
  ["IDLE", "Worker prêt, modèle déchargé", "bg-success"],
];

export const systemPollDelay = (status: SystemStatus | null) =>
  status && status.queue.queued + status.queue.running > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS;

function workerSummary(workers: Worker[]) {
  if (workers.length === 0) return { label: "Aucun worker actif", dot: "bg-danger" };
  const rank = (worker: Worker) => {
    const index = WORKER_STATES.findIndex(([state]) => state === worker.state);
    return index === -1 ? WORKER_STATES.length : index;
  };
  const worst = workers.reduce((a, b) => (rank(b) < rank(a) ? b : a));
  const known = WORKER_STATES[rank(worst)];
  const label = known?.[1] ?? worst.state;
  return { label: workers.length > 1 ? `${label} (${workers.length} workers)` : label, dot: known?.[2] ?? "bg-accent" };
}

function vramSummary({ gpu, workers }: SystemStatus) {
  if (gpu.free_vram_bytes === null) return "VRAM inconnue";
  const free = gigabytes.format(gpu.free_vram_bytes / GIB);
  const total = workers.find((worker) => worker.gpu.vram_total_bytes !== null)?.gpu.vram_total_bytes;
  return total ? `VRAM libre ${free} / ${gigabytes.format(total / GIB)} Go` : `VRAM libre ${free} Go`;
}

function dispatchSummary({ paused_by, reason }: SystemStatus["dispatch"]) {
  if (paused_by !== "system") return "Distribution suspendue";
  return reason ? `Distribution suspendue par le système : ${reason}` : "Distribution suspendue par le système";
}

type SystemBarProps = { api?: SystemApi; refreshKey?: number; onDispatchChanged?: () => void };

export function SystemBar({ api = defaultApi, refreshKey = 0, onDispatchChanged }: SystemBarProps) {
  // `refreshKey` force une lecture immédiate après une action.
  const load = useCallback((signal: AbortSignal) => api.status(signal), [api, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const { data, failed, refresh } = usePolling(load, systemPollDelay);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);

  async function toggleDispatch(paused: boolean) {
    setPending(true);
    setError(false);
    try {
      await api.setDispatch({ paused });
      refresh();
      onDispatchChanged?.();
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  }

  if (failed) return <span role="status" className="text-danger">Le service ne répond pas.</span>;
  if (!data) return <span>Lecture de l&apos;état du système…</span>;

  const worker = workerSummary(data.workers);
  const { paused } = data.dispatch;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <span className="flex items-center gap-1.5">
        <span aria-hidden="true" className={`size-2 rounded-full ${worker.dot}`} />
        <span>{worker.label}</span>
      </span>
      <span>{vramSummary(data)}</span>
      {data.gpu.vram_limit_bytes !== null && <span>{`Plafond ${gigabytes.format(data.gpu.vram_limit_bytes / GIB)} Go`}</span>}
      <span>{`${data.queue.queued} en file`}</span>
      {data.queue.running > 0 && <span>{`${data.queue.running} en cours`}</span>}
      {paused && <span className="max-w-80 truncate font-medium text-accent">{dispatchSummary(data.dispatch)}</span>}
      <button
        type="button"
        disabled={pending}
        className="min-h-9 rounded-md border border-line-strong bg-surface px-3 font-sans text-[13px] text-ink disabled:opacity-50 max-sm:hidden"
        onClick={() => toggleDispatch(!paused)}
      >
        {paused ? "Reprendre" : "Suspendre"}
      </button>
      {error && (
        <span role="alert" className="text-danger">
          La distribution n&apos;a pas pu être modifiée.
        </span>
      )}
    </div>
  );
}
