import type { Generation } from "@/lib/api/client";

export const STAGES: Record<string, string> = {
  preprocess: "Prétraitement",
  encode: "Encodage",
  sampling: "Échantillonnage",
  decode: "Décodage",
  export: "Export",
};

export const STATUS_LABELS: Record<Generation["status"], string> = {
  PENDING: "Import",
  QUEUED: "En file",
  RUNNING: "En cours",
  COMPLETED: "Terminé",
  FAILED: "Échoué",
  CANCELLED: "Annulé",
};

const STATUS_TONES: Record<Generation["status"], string> = {
  PENDING: "bg-page text-ink-muted",
  QUEUED: "bg-page text-ink-muted",
  RUNNING: "bg-progress-soft text-progress",
  COMPLETED: "bg-success-soft text-success",
  FAILED: "bg-failure-soft text-failure",
  CANCELLED: "bg-page text-ink-muted",
};

export const badgeClass = (status: Generation["status"]) =>
  `shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_TONES[status]}`;

export const isActive = (status: Generation["status"]) => status === "PENDING" || status === "QUEUED" || status === "RUNNING";

export const ACTIVE_POLL_MS = 2_000;
export const IDLE_POLL_MS = 15_000;

export function formatElapsed(milliseconds: number) {
  const total = Math.floor(milliseconds / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}
