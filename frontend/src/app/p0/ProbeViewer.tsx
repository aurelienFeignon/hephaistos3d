"use client";

import { useEffect, useState } from "react";
import { SplatViewer, type IdolCamera } from "@/components/SplatViewer";
import { downloadArtifact } from "@/lib/artifacts/download";

type Loaded = { bytes: Uint8Array; camera: IdolCamera | null };

export function ProbeViewer({ label }: { label: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const camera = fetch("/p0/camera.json", { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<IdolCamera>) : null))
      .catch(() => null);
    downloadArtifact("/p0/splat.ply", { signal: controller.signal })
      .then(async (bytes) => setLoaded({ bytes, camera: await camera }))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(String(reason));
      });
    return () => controller.abort();
  }, []);
  if (error) return <p role="alert">{error}</p>;
  if (!loaded) return <p role="status">Chargement…</p>;
  return <SplatViewer {...loaded} label={label} onError={(kind) => setError(`Échec de la scène : ${kind}`)} />;
}
