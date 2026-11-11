"use client";

import Image from "next/image";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { createApiClient, type Generation } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { useResource } from "@/lib/api/useResource";
import { formatValue, parseSchema, type FormField, type ParamValue } from "@/lib/forms/engine";
import { GenerationActions, type ActionsApi } from "./GenerationActions";
import { ACTIVE_POLL_MS, badgeClass, formatMegabytes, IDLE_POLL_MS, isActive, STAGES, STATUS_LABELS } from "@/lib/generations/format";

const defaultApi = createApiClient();
const LOG_TAIL = 200;
const LOG_HEADER_FIELDS = new Set(["timestamp", "event", "generation_id"]);
const seconds = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export type DetailApi = Pick<typeof defaultApi, "generation" | "attempts" | "logs" | "modelSchema"> & ActionsApi;
type Attempt = Awaited<ReturnType<DetailApi["attempts"]>>["items"][number];

const OUTCOMES: Record<string, string> = {
  SUCCEEDED: "réussie",
  FAILED: "échec",
  CANCELLED: "annulée",
  INTERRUPTED: "interrompue",
  ABANDONED: "abandonnée",
};

// Le libellé garde sa largeur naturelle (au plus la moitié) : une colonne de valeurs en `auto`
// prenait la largeur de la plus longue valeur et réduisait les libellés à une lettre par ligne.
const definitionListClass = "grid grid-cols-[fit-content(50%)_minmax(0,1fr)] gap-x-3 gap-y-1 text-[13px]";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <h3 className="text-[13px] font-semibold tracking-[0.06em] text-ink-muted uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Pair({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="min-w-0 break-words text-ink-muted">{label}</dt>
      <dd className="min-w-0 text-right font-mono break-words">{children}</dd>
    </>
  );
}

type Artifact = Generation["artifacts"][number];
const secondaryLink = "flex min-h-11 flex-1 items-center justify-center rounded-md border border-line-strong px-3";

// Le nom du fichier vient de Content-Disposition : `download` reste sans valeur pour ne pas l'écraser.
function Downloads({ artifacts }: { artifacts: Artifact[] }) {
  const of = (kind: Artifact["kind"]) => artifacts.find((artifact) => artifact.kind === kind);
  const ply = of("SPLAT_PLY");
  const preview = of("PREVIEW_PNG");
  const camera = of("CAMERA_JSON");
  if (!ply && !preview && !camera) return null;
  return (
    <Section title="Télécharger">
      {ply && (
        <a
          href={ply.download_url}
          download
          aria-label={`Télécharger splat.ply (${formatMegabytes(ply.size_bytes)} Mo)`}
          className="flex min-h-11 items-center justify-between rounded-md bg-accent px-3 font-medium text-white"
        >
          splat.ply<span className="font-mono text-xs">{`${formatMegabytes(ply.size_bytes)} Mo`}</span>
        </a>
      )}
      {(preview || camera) && (
        <div className="flex gap-2">
          {preview && (
            <a href={preview.download_url} download aria-label="Télécharger l'aperçu PNG" className={secondaryLink}>
              Aperçu PNG
            </a>
          )}
          {camera && (
            <a href={camera.download_url} download aria-label="Télécharger camera.json" className={secondaryLink}>
              camera.json
            </a>
          )}
        </div>
      )}
    </Section>
  );
}

function paramRows(params: Generation["params"], fields: FormField[]) {
  const known = fields.filter((field) => field.name in params).map((field) => ({ name: field.name, label: field.label, text: formatValue(field, params[field.name] as ParamValue) }));
  const names = new Set(fields.map((field) => field.name));
  const unknown = Object.entries(params).filter(([name]) => !names.has(name)).map(([name, value]) => ({ name, label: name, text: String(value) }));
  return [...known, ...unknown];
}

function Parameters({ api, generation }: { api: DetailApi; generation: Generation }) {
  const load = useCallback(async (signal: AbortSignal) => parseSchema(await api.modelSchema(generation.model_id, signal)), [api, generation.model_id]);
  const { state } = useResource(load);
  const fields = state.status === "ready" ? state.data : [];
  return (
    <Section title="Paramètres effectifs">
      <dl className={definitionListClass}>
        <Pair label="Modèle">{`${generation.model_id} (${generation.model_version})`}</Pair>
        {generation.preset && <Pair label="Préréglage">{generation.preset}</Pair>}
        {paramRows(generation.params, fields).map(({ name, label, text }) => (
          <Pair key={name} label={label}>
            {text}
          </Pair>
        ))}
        <Pair label="Graine">{generation.seed}</Pair>
      </dl>
    </Section>
  );
}

function StageDurations({ attempt }: { attempt: Attempt }) {
  const durations = attempt.stage_durations_ms;
  const stages = Object.keys(STAGES).filter((stage) => typeof durations[stage] === "number");
  if (stages.length === 0) return null;
  return (
    <Section title="Durées par étape">
      <dl className={definitionListClass}>
        {stages.map((stage) => (
          <Pair key={stage} label={STAGES[stage]}>{`${seconds.format((durations[stage] as number) / 1000)} s`}</Pair>
        ))}
      </dl>
    </Section>
  );
}

function logLine(entry: Record<string, unknown>) {
  const { timestamp, event } = entry;
  const details = Object.entries(entry).filter(([key]) => !LOG_HEADER_FIELDS.has(key));
  const time = typeof timestamp === "string" ? timestamp.slice(11, 19) : "";
  return [time, event, details.length ? JSON.stringify(Object.fromEntries(details)) : ""].filter(Boolean).join(" ");
}

function AttemptLog({ api, generationId, number }: { api: DetailApi; generationId: string; number: number }) {
  const load = useCallback((signal: AbortSignal) => api.logs(generationId, { attempt: number, tail: LOG_TAIL }, signal), [api, generationId, number]);
  const { state } = useResource(load);
  if (state.status === "loading") return <p className="text-[13px] text-ink-muted">Lecture du journal…</p>;
  if (state.status === "error") {
    return (
      <p role="alert" className="text-[13px] text-danger">
        Impossible de lire ce journal.
      </p>
    );
  }
  if (state.data.length === 0) return <p className="text-[13px] text-ink-muted">Ce journal est vide.</p>;
  return (
    <pre className="max-h-64 overflow-auto rounded-md bg-page p-2 font-mono text-[11px] break-all whitespace-pre-wrap">
      {state.data.map(logLine).join("\n")}
    </pre>
  );
}

function AttemptRow({ api, generationId, attempt }: { api: DetailApi; generationId: string; attempt: Attempt }) {
  const [showLog, setShowLog] = useState(false);
  const outcome = attempt.outcome ? (OUTCOMES[attempt.outcome] ?? attempt.outcome) : "en cours";
  return (
    <li className="flex flex-col gap-1 text-[13px]">
      <span className="font-medium">{`Tentative ${attempt.number} : ${outcome}`}</span>
      {attempt.error_code && <span className="break-words text-danger">{[attempt.error_code, attempt.error_message].filter(Boolean).join(" : ")}</span>}
      <button
        type="button"
        aria-label={`Voir le journal de la tentative ${attempt.number}`}
        aria-expanded={showLog}
        className="min-h-11 self-start text-ink-muted underline"
        onClick={() => setShowLog((value) => !value)}
      >
        Journal
      </button>
      {showLog && <AttemptLog api={api} generationId={generationId} number={attempt.number} />}
    </li>
  );
}

function Details({ api, generation, onClose, onChanged, onDeleted }: { api: DetailApi; generation: Generation; onClose: () => void; onChanged: () => void; onDeleted: () => void }) {
  const active = isActive(generation.status);
  const { id, source, error } = generation;
  const loadAttempts = useCallback((signal: AbortSignal) => api.attempts(id, undefined, signal), [api, id]);
  const delayOf = useMemo(() => () => (active ? ACTIVE_POLL_MS : IDLE_POLL_MS), [active]);
  const { data: attempts } = usePolling(loadAttempts, delayOf);
  const latest = attempts?.items.at(-1);
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <Image unoptimized src={source.url} alt="Image d'entrée" width={64} height={64} className="size-16 shrink-0 rounded-md bg-page object-cover" />
        <div className="flex min-w-0 flex-col items-start gap-1">
          <span className="max-w-full truncate font-medium">{source.filename}</span>
          <span className={badgeClass(generation.status)}>{STATUS_LABELS[generation.status]}</span>
          <span className="font-mono text-xs text-ink-faint">{`${source.width} × ${source.height}`}</span>
        </div>
      </div>

      {generation.status === "COMPLETED" && <Downloads artifacts={generation.artifacts} />}
      {error && (
        <Section title="Erreur">
          <p className="font-mono text-[13px] text-danger">{error.code}</p>
          {error.message && <p className="text-[13px] break-words">{error.message}</p>}
          {error.needs_diagnosis && <p className="text-[13px] text-ink-muted">Cette erreur demande un diagnostic avant toute relance. Consultez le journal.</p>}
        </Section>
      )}
      <Parameters api={api} generation={generation} />
      {latest && <StageDurations attempt={latest} />}
      {attempts && attempts.items.length > 0 && (
        <Section title="Tentatives">
          <ul className="flex flex-col gap-2">
            {attempts.items.map((attempt) => (
              <AttemptRow key={attempt.id} api={api} generationId={id} attempt={attempt} />
            ))}
          </ul>
        </Section>
      )}
      <GenerationActions api={api} generation={generation} onChanged={onChanged} onDeleted={onDeleted} />
      <button type="button" className="min-h-11 rounded-md border border-line-strong px-3" onClick={onClose}>
        Créer une génération
      </button>
    </div>
  );
}

type GenerationDetailProps = { api?: DetailApi; id: string; onClose: () => void; onChanged?: () => void; onDeleted?: () => void };

export function GenerationDetail({ api = defaultApi, id, onClose, onChanged, onDeleted = onClose }: GenerationDetailProps) {
  const load = useCallback((signal: AbortSignal) => api.generation(id, signal), [api, id]);
  const delayOf = useCallback((generation: Generation | null) => (generation && isActive(generation.status) ? ACTIVE_POLL_MS : IDLE_POLL_MS), []);
  const { data, failed, refresh } = usePolling(load, delayOf);
  const changed = () => {
    refresh();
    onChanged?.();
  };
  if (data) return <Details api={api} generation={data} onClose={onClose} onChanged={changed} onDeleted={onDeleted} />;
  if (!failed) return <p className="text-[13px] text-ink-muted">Chargement du détail…</p>;
  return (
    <div className="flex flex-col items-start gap-2">
      <p role="status" className="text-[13px] text-danger">
        Impossible de charger cette génération. Nouvelle tentative automatique.
      </p>
      <button type="button" className="min-h-11 rounded-md border border-line-strong px-3" onClick={onClose}>
        Créer une génération
      </button>
    </div>
  );
}
