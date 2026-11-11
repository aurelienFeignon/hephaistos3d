"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState, type ComponentType, type ReactNode } from "react";
import type { IdolCamera, SceneError } from "@/components/SplatViewer";
import { createApiClient, type Generation } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { ArtifactLoadError, downloadArtifact, isPly } from "@/lib/artifacts/download";
import { ACTIVE_POLL_MS, formatMegabytes as mo, IDLE_POLL_MS, isActive } from "@/lib/generations/format";

export type { IdolCamera };
export type SceneProps = { bytes: Uint8Array; camera: IdolCamera | null; label: string; onError: (kind: SceneError) => void };
export type ViewerApi = Pick<ReturnType<typeof createApiClient>, "generation" | "downloadUrl">;

// Spark et Three.js ne se chargent que dans le navigateur, et seulement quand un résultat s'affiche (P4.1).
const SplatViewer = dynamic(() => import("@/components/SplatViewer").then((module) => module.SplatViewer), {
  ssr: false,
  loading: () => <Message>Préparation de la scène…</Message>,
});

const defaultApi = createApiClient();

const MESSAGES = {
  INVALID: "Le fichier 3D est illisible : ce n'est pas un PLY valide.",
  MISSING: "Le fichier 3D est introuvable sur le serveur : il a peut-être été supprimé.",
  WEBGL: "WebGL est indisponible dans ce navigateur : la scène 3D ne peut pas s'afficher.",
} as const;

type Load =
  | { status: "downloading"; loaded: number; total: number | null }
  | { status: "ready"; bytes: Uint8Array; camera: IdolCamera | null }
  | { status: "error"; message: string; retry: boolean };

function Message({ children, alert = false }: { children: ReactNode; alert?: boolean }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center" role={alert ? "alert" : "status"}>
      {children}
    </div>
  );
}

function parseCamera(bytes: Uint8Array): IdolCamera | null {
  try {
    const value = JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
    const fields = ["yaw", "pitch", "radius", "fov"] as const;
    if (!fields.every((field) => typeof value[field] === "number" && Number.isFinite(value[field]))) return null;
    return { yaw: value.yaw as number, pitch: value.pitch as number, radius: value.radius as number, fov: value.fov as number };
  } catch {
    return null;
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof ArtifactLoadError) {
    return error.kind === "MISSING" ? MESSAGES.MISSING : `Le téléchargement du fichier 3D a échoué. ${error.message}`;
  }
  return "Le téléchargement du fichier 3D a échoué.";
}

type Props = {
  id: string | null;
  api?: ViewerApi;
  download?: typeof downloadArtifact;
  Scene?: ComponentType<SceneProps>;
};

export function ResultViewer({ id, ...rest }: Props) {
  if (id === null) return <Message>Sélectionnez une génération terminée dans la bibliothèque pour l&apos;afficher en 3D.</Message>;
  // La clé remet l'état à zéro et abandonne tout téléchargement en changeant de génération.
  return <GenerationResult key={id} id={id} {...rest} />;
}

function GenerationResult({ id, api = defaultApi, download = downloadArtifact, Scene = SplatViewer }: Props & { id: string }) {
  const loadGeneration = useCallback((signal: AbortSignal) => api.generation(id, signal), [api, id]);
  const delayOf = useCallback((generation: Generation | null) => (generation && isActive(generation.status) ? ACTIVE_POLL_MS : IDLE_POLL_MS), []);
  const { data: generation, failed } = usePolling(loadGeneration, delayOf);

  const ply = generation?.status === "COMPLETED" ? generation.artifacts.find((artifact) => artifact.kind === "SPLAT_PLY") : undefined;
  const cameraArtifact = generation?.artifacts.find((artifact) => artifact.kind === "CAMERA_JSON");
  const plyId = ply?.id;
  const plySize = ply?.size_bytes;
  const cameraId = cameraArtifact?.id;

  const [load, setLoad] = useState<Load>({ status: "downloading", loaded: 0, total: null });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!plyId) return;
    const controller = new AbortController();
    const { signal } = controller;
    const camera = cameraId
      ? download(api.downloadUrl(cameraId), { signal }).then(parseCamera, () => null)
      : Promise.resolve(null);
    download(api.downloadUrl(plyId), {
      signal,
      expectedBytes: plySize,
      onProgress: (loaded, total) => {
        if (!signal.aborted) setLoad({ status: "downloading", loaded, total });
      },
    })
      .then(async (bytes) => {
        if (signal.aborted) return;
        if (!isPly(bytes)) {
          setLoad({ status: "error", message: MESSAGES.INVALID, retry: false });
          return;
        }
        const view = await camera;
        if (!signal.aborted) setLoad({ status: "ready", bytes, camera: view });
      })
      .catch((error: unknown) => {
        if (!signal.aborted) setLoad({ status: "error", message: errorMessage(error), retry: true });
      });
    return () => controller.abort();
  }, [api, download, plyId, plySize, cameraId, attempt]);

  const onSceneError = useCallback((kind: SceneError) => {
    setLoad({ status: "error", message: kind === "WEBGL" ? MESSAGES.WEBGL : MESSAGES.INVALID, retry: false });
  }, []);

  if (!generation) return <Message>{failed ? "Impossible de joindre le serveur." : "Chargement…"}</Message>;
  if (isActive(generation.status)) return <Message>Le résultat s&apos;affichera à la fin de la génération.</Message>;
  if (!ply) return <Message>Cette génération n&apos;a pas produit de résultat 3D.</Message>;

  if (load.status === "error") {
    return (
      <Message alert>
        <p className="max-w-md">{load.message}</p>
        {load.retry && (
          <button type="button" className="min-h-11 rounded-md border border-current/30 px-4" onClick={() => {
              setLoad({ status: "downloading", loaded: 0, total: null });
              setAttempt((value) => value + 1);
            }}>
            Réessayer
          </button>
        )}
      </Message>
    );
  }
  if (load.status === "downloading") {
    const percent = load.total ? Math.min(100, Math.round((load.loaded / load.total) * 100)) : undefined;
    return (
      <Message>
        <p>Téléchargement du fichier 3D</p>
        <div
          role="progressbar"
          aria-label="Téléchargement du fichier 3D"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="h-1.5 w-64 max-w-full overflow-hidden rounded-full bg-current/15"
        >
          <div className={`h-full bg-current/70 ${percent === undefined ? "w-1/3 animate-pulse" : ""}`} style={percent === undefined ? undefined : { width: `${percent}%` }} />
        </div>
        <p className="font-mono text-xs">{load.total ? `${mo(load.loaded)} / ${mo(load.total)} Mo` : `${mo(load.loaded)} Mo`}</p>
      </Message>
    );
  }
  return <Scene bytes={load.bytes} camera={load.camera} label={generation.source.filename} onError={onSceneError} />;
}
