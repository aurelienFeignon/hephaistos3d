"use client";

import { useState } from "react";
import { ApiError, createApiClient, type Generation } from "@/lib/api/client";
import { isActive } from "@/lib/generations/format";

const defaultApi = createApiClient();

export type ActionsApi = Pick<typeof defaultApi, "cancel" | "retry" | "protect" | "deleteGeneration">;

const ERROR_MESSAGES: Record<string, string> = {
  INVALID_TRANSITION: "L'état de cette génération a changé. Vérifiez-le puis réessayez.",
  GENERATION_RUNNING: "Annulez cette génération avant de la supprimer.",
  GENERATION_NOT_FOUND: "Cette génération n'existe plus.",
  UPLOAD_CAPACITY_EXCEEDED: "Le serveur est occupé. Réessayez dans un instant.",
};

const buttonClass = "min-h-11 flex-auto rounded-lg border border-line-strong bg-surface px-3 disabled:opacity-50 aria-pressed:border-accent aria-pressed:text-accent";
const dangerClass = "min-h-11 flex-auto rounded-lg border border-danger/40 bg-surface px-3 text-danger disabled:opacity-50";

type GenerationActionsProps = {
  api?: ActionsApi;
  generation: Generation;
  onChanged: () => void;
  onDeleted: () => void;
};

export function GenerationActions({ api = defaultApi, generation, onChanged, onDeleted }: GenerationActionsProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // L'API ne publie pas qu'une annulation coopérative est en attente.
  const [cancelRequested, setCancelRequested] = useState(false);
  const { id, status } = generation;

  async function run(action: () => Promise<void>) {
    setPending(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (cause) {
      setError((cause instanceof ApiError && ERROR_MESSAGES[cause.code]) || "L'action a échoué. Réessayez.");
      onChanged();
    } finally {
      setPending(false);
    }
  }

  async function remove() {
    setPending(true);
    setError(null);
    try {
      await api.deleteGeneration(id);
      onDeleted();
    } catch (cause) {
      setConfirmingDelete(false);
      setError((cause instanceof ApiError && ERROR_MESSAGES[cause.code]) || "L'action a échoué. Réessayez.");
      onChanged();
    } finally {
      setPending(false);
    }
  }

  const importFailed = generation.error?.code === "INGEST_FAILED";
  const canRetry = (status === "FAILED" || status === "CANCELLED") && !importFailed;
  const showCancelRequested = cancelRequested && status === "RUNNING";

  return (
    <div className="flex flex-col gap-2">
      {confirmingDelete ? (
        <div className="flex flex-col gap-2">
          <p className="text-[13px]">
            {`Supprimer définitivement cette génération et ses fichiers ?${generation.protected ? " Elle est protégée." : ""}`}
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" autoFocus disabled={pending} className={buttonClass} onClick={() => setConfirmingDelete(false)}>
              Conserver
            </button>
            <button type="button" disabled={pending} className={dangerClass} onClick={remove}>
              Confirmer la suppression
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {isActive(status) && (
            <button
              type="button"
              disabled={pending || showCancelRequested}
              className={buttonClass}
              onClick={() =>
                run(async () => {
                  const updated = await api.cancel(id);
                  setCancelRequested(updated.status === "RUNNING");
                })
              }
            >
              {showCancelRequested ? "Annulation demandée" : "Annuler"}
            </button>
          )}
          {canRetry && (
            <button
              type="button"
              disabled={pending}
              className={buttonClass}
              onClick={() =>
                run(async () => {
                  setCancelRequested(false);
                  await api.retry(id);
                })
              }
            >
              Relancer
            </button>
          )}
          <button
            type="button"
            aria-pressed={generation.protected}
            disabled={pending}
            className={buttonClass}
            onClick={() =>
              run(async () => {
                await api.protect(id, !generation.protected);
              })
            }
          >
            Protéger
          </button>
          {status !== "RUNNING" && (
            <button type="button" disabled={pending} className={dangerClass} onClick={() => setConfirmingDelete(true)}>
              Supprimer
            </button>
          )}
        </div>
      )}
      {importFailed && <p className="text-[13px] text-ink-muted">Importez l&apos;image de nouveau pour relancer.</p>}
      {error && (
        <p role="alert" className="text-[13px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
