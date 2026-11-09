"use client";

import Image from "next/image";
import { useCallback, useMemo, useState } from "react";
import { createApiClient, type Generation, type GenerationQuery } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { ACTIVE_POLL_MS, badgeClass, formatElapsed, IDLE_POLL_MS, STATUS_LABELS } from "@/lib/generations/format";
import { fieldInputClass, fieldLabelClass } from "./ParamsForm";

const defaultApi = createApiClient();
const PAGE_SIZE = 30;
const API_MAX_LIMIT = 100;

export type LibraryApi = Pick<typeof defaultApi, "generations">;

const FILTERS: Record<string, { label: string; query: GenerationQuery }> = {
  all: { label: "Toutes", query: {} },
  active: { label: "En cours", query: { status: ["QUEUED", "RUNNING"] } },
  completed: { label: "Terminées", query: { status: ["COMPLETED"] } },
  failed: { label: "Échouées", query: { status: ["FAILED"] } },
  cancelled: { label: "Annulées", query: { status: ["CANCELLED"] } },
  protected: { label: "Protégées", query: { protected: true } },
};

type ListProps = { api: LibraryApi; query: GenerationQuery; selectedId: string | null; onSelect: (id: string) => void; hasActive: boolean; refreshKey: number };

function metaOf(item: Generation) {
  const parts = item.error
    ? [item.error.code, `${item.attempt_count} ${item.attempt_count > 1 ? "tentatives" : "tentative"}`]
    : [item.model_id, ...(item.elapsed_ms !== null ? [formatElapsed(item.elapsed_ms)] : [])];
  return [...parts, ...(item.protected ? ["protégée"] : [])].join(" · ");
}

function Row({ item, selected, onSelect }: { item: Generation; selected: boolean; onSelect: (id: string) => void }) {
  const { current, total } = item.progress;
  const badge = item.status === "RUNNING" && current !== null && total !== null ? `${current}/${total}` : STATUS_LABELS[item.status];
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(item.id)}
        className="flex min-h-11 w-full items-center gap-2.5 rounded-lg p-2 text-left hover:bg-page aria-pressed:bg-page aria-pressed:outline-2 aria-pressed:-outline-offset-2 aria-pressed:outline-accent"
      >
        <Image unoptimized src={item.source.url} alt="" width={40} height={40} className="size-10 shrink-0 rounded-md bg-page object-cover" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate font-medium">{item.source.filename}</span>
          <span className="truncate font-mono text-[11px] text-ink-faint">{metaOf(item)}</span>
        </span>
        <span className={badgeClass(item.status)}>{badge}</span>
      </button>
    </li>
  );
}

function LibraryList({ api, query, selectedId, onSelect, hasActive, refreshKey }: ListProps) {
  // Tout ce qui est affiché se relit à chaque cycle, sinon une génération ancienne resterait figée.
  const [limit, setLimit] = useState(PAGE_SIZE);
  // `refreshKey` force une lecture immédiate après une action.
  const load = useCallback((signal: AbortSignal) => api.generations({ limit, ...query }, signal), [api, limit, query, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const delayOf = useMemo(() => () => (hasActive ? ACTIVE_POLL_MS : IDLE_POLL_MS), [hasActive]);
  const { data, failed } = usePolling(load, delayOf);
  const [more, setMore] = useState<{ items: Generation[]; cursor: string | null } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  const [seenRefresh, setSeenRefresh] = useState(refreshKey);
  if (refreshKey !== seenRefresh) {
    setSeenRefresh(refreshKey);
    setMore(null); // ces pages ne se relisent pas et peuvent contenir une ligne supprimée
  }

  const items = [...new Map([...(data?.items ?? []), ...(more?.items ?? [])].map((item) => [item.id, item])).values()];
  const cursor = more ? more.cursor : (data?.next_cursor ?? null);

  async function loadMore() {
    if (!cursor) return;
    if (limit < API_MAX_LIMIT) {
      setLimit(Math.min(API_MAX_LIMIT, limit + PAGE_SIZE));
      return;
    }
    setLoadingMore(true);
    setMoreFailed(false);
    try {
      const page = await api.generations({ limit: PAGE_SIZE, ...query, cursor });
      setMore((current) => ({ items: [...(current?.items ?? []), ...page.items], cursor: page.next_cursor }));
    } catch {
      setMoreFailed(true);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <>
      {data === null && !failed && <p className="text-ink-muted">Chargement de la bibliothèque…</p>}
      {data !== null && items.length === 0 && <p className="text-ink-muted">Aucune génération pour ce filtre.</p>}
      {failed && (
        <p role="status" className="text-[13px] text-danger">
          La bibliothèque ne répond pas. Nouvelle tentative automatique.
        </p>
      )}
      {items.length > 0 && (
        <ul className="flex flex-col gap-1">
          {items.map((item) => (
            <Row key={item.id} item={item} selected={item.id === selectedId} onSelect={onSelect} />
          ))}
        </ul>
      )}
      {moreFailed && (
        <p role="alert" className="text-[13px] text-danger">
          Impossible de charger la suite. Réessayez.
        </p>
      )}
      {cursor && (
        <button type="button" disabled={loadingMore} className="min-h-11 shrink-0 rounded-md border border-line-strong px-3 disabled:opacity-50" onClick={loadMore}>
          Afficher plus
        </button>
      )}
    </>
  );
}

type LibraryProps = { api?: LibraryApi; selectedId: string | null; onSelect: (id: string) => void; hasActive: boolean; refreshKey?: number };

export function Library({ api = defaultApi, selectedId, onSelect, hasActive, refreshKey = 0 }: LibraryProps) {
  const [filter, setFilter] = useState("all");
  return (
    <>
      <label className={fieldLabelClass}>
        Statut
        <select className={`${fieldInputClass} text-ink`} value={filter} onChange={(event) => setFilter(event.target.value)}>
          {Object.entries(FILTERS).map(([key, { label }]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <LibraryList key={filter} api={api} query={FILTERS[filter].query} selectedId={selectedId} onSelect={onSelect} hasActive={hasActive} refreshKey={refreshKey} />
    </>
  );
}
