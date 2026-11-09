import type { QueueItem, QueueView } from "@/lib/api/client";

const ACTIVE_POLL_MS = 2_000;
const IDLE_POLL_MS = 15_000;

export const queuePollDelay = (queue: QueueView | null) => (queue?.items.length ? ACTIVE_POLL_MS : IDLE_POLL_MS);

const STAGES: Record<string, string> = {
  preprocess: "Prétraitement",
  encode: "Encodage",
  sampling: "Échantillonnage",
  decode: "Décodage",
  export: "Export",
};

const WAITING_REASONS: Record<string, string> = {
  VRAM_INSUFFICIENT: "attend de la mémoire graphique",
  DISPATCH_PAUSED: "distribution suspendue",
  NO_WORKER: "aucun worker actif",
  RETRY_BACKOFF: "relance programmée",
};

function formatElapsed(milliseconds: number) {
  const total = Math.floor(milliseconds / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

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

export function QueueBar({ items, failed }: { items: QueueItem[] | null; failed: boolean }) {
  return (
    <>
      {items && items.length > 0 && (
        <ul className="flex max-h-16 min-w-0 flex-1 flex-wrap items-center gap-x-6 gap-y-1 overflow-y-auto">
          {items.map((item) => (
            <li key={item.id} className="flex min-w-0 items-center gap-2.5">
              {item.position !== null && <span className="font-mono text-xs text-ink-muted">{`#${item.position}`}</span>}
              <span className="max-w-48 truncate font-medium">{item.source.filename}</span>
              {item.status === "RUNNING" ? <Running item={item} /> : <Waiting item={item} />}
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
