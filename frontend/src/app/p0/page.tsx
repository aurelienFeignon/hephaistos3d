import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SplatViewer } from "@/components/SplatViewer";
import { PROBE_RUN } from "./probeFile";

export const metadata: Metadata = { title: "Gate P0 — visualisation" };

// G0.7 : un PLY produit par la sonde s'affiche dans Spark. Ce n'est pas l'interface (P4).
export default function P0Page() {
  if (process.env.NODE_ENV === "production" || process.env.HEPHAISTOS_ENABLE_PROBES !== "true") {
    notFound();
  }
  return (
    <main
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: 16,
        minHeight: "100dvh",
        boxSizing: "border-box",
      }}
    >
      <h1 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>
        Gate P0 — visualisation Spark
      </h1>
      <div
        style={{ flex: "1 1 auto", display: "flex", flexDirection: "column" }}
      >
        <SplatViewer
          splatUrl="/p0/splat.ply"
          cameraUrl="/p0/camera.json"
          label={`${PROBE_RUN} · splat.ply`}
        />
      </div>
    </main>
  );
}
