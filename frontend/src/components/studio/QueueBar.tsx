"use client";

import { useState } from "react";
import type { QueueItem, QueueView } from "@/lib/api/client";
import { ACTIVE_POLL_MS, formatElapsed, IDLE_POLL_MS, STAGES } from "@/lib/generations/format";

export const queuePollDelay = (queue: QueueView | null) => (queue?.items.length ? ACTIVE_POLL_MS : IDLE_POLL_MS);

const WAITING_REASONS: Record<string, string> = {
  VRAM_INSUFFICIENT: "attend de la mémoire graphique",
  DISPATCH_PAUSED: "distribution suspendue",
  NO_WORKER: "aucun worker actif",
  RETRY_BACKOFF: "relance programmée",
};

function Running({ item }: { item: QueueItem }) {
  const { stage, current, total } = item.progress;
  const stageLabel = stage ? (STAGES[stage] ?? stage) : "Démarrage";
  const hasSteps = current !== null && total !== null && total > 0;
  return (
    <>
      <span className="font-mono text-xs text-ink-muted">{hasSteps ? `${stageLabel} ${current}/${total}` : stageLabel}</span>
      {item.elapsed_ms !== null && <span className="font-mono text-xs text-ink-muted">{formatElapsed(item.elapsed_ms)}</span>}
      {hasSteps && (
        <span
          role="progressbar"
          aria-label={`Progression de ${item.source.filename}`}
          aria-valuemin={0}
          aria-valuenow={current}
          aria-valuemax={total}
          className="inline-flex h-1.5 w-[120px] overflow-hidden rounded-full bg-page"
        >
          <span className="bg-accent" style={{ width: `${(current / total) * 100}%` }} />
        </span>
      )}
    </>
  );
}

function Waiting({ item }: { item: QueueItem }) {
  const reason = (item.waiting_reason && WAITING_REASONS[item.waiting_reason]) || "en attente";
  return <span className="font-mono text-xs text-ink-muted">{reason}</span>;
}

function CancelButton({ item, onCancel }: { item: QueueItem; onCancel: (id: string) => Promise<unknown> }) {
  const [state, setState] = useState<"idle" | "requested" | "failed">("idle");
  async function cancel() {
    setState("requested");
    try {
      await onCancel(item.id);
    } catch {
      setState("failed");
    }
  }
  return (
    <>
      <button
        type="button"
        aria-label={`Annuler ${item.source.filename}`}
        disabled={state === "requested"}
        className="min-h-9 shrink-0 rounded-md border border-line-strong px-3 disabled:opacity-50"
        onClick={cancel}
      >
        {state === "requested" ? "Annulation demandée" : "Annuler"}
      </button>
      {state === "failed" && (
        <span role="alert" className="text-danger">
          Annulation impossible.
        </span>
      )}
    </>
  );
}

type QueueBarProps = { items: QueueItem[] | null; failed: boolean; onCancel?: (id: string) => Promise<unknown> };

export function QueueBar({ items, failed, onCancel }: QueueBarProps) {
  return (
    <>
      {items && items.length > 0 && (
        <ul className="flex max-h-16 min-w-0 flex-1 flex-wrap items-center gap-x-6 gap-y-1 overflow-y-auto">
          {items.map((item) => (
            <li key={item.id} className="flex min-w-0 items-center gap-2.5">
              {item.position !== null && <span className="font-mono text-xs text-ink-muted">{`#${item.position}`}</span>}
              <span className="max-w-48 truncate font-medium">{item.source.filename}</span>
              {item.status === "RUNNING" ? <Running item={item} /> : <Waiting item={item} />}
              {onCancel && <CancelButton item={item} onCancel={onCancel} />}
            </li>
          ))}
        </ul>
      )}
      {items?.length === 0 && <span className="text-ink-muted">Aucune génération en cours ni en attente.</span>}
      {items === null && !failed && <span className="text-ink-muted">Chargement de la file…</span>}
      {failed && (
        <span role="status" className="text-danger">
          La file ne répond pas. Nouvelle tentative automatique.
        </span>
      )}
    </>
  );
}
