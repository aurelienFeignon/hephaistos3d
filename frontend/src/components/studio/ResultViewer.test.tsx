// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Generation } from "@/lib/api/client";
import { ArtifactLoadError } from "@/lib/artifacts/download";
import { ResultViewer, type SceneProps, type ViewerApi } from "./ResultViewer";

beforeEach(() => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const PLY = new TextEncoder().encode("ply\nformat binary_little_endian 1.0\nend_header\n");
const CAMERA = { yaw: 0.5, pitch: 0.1, radius: 2.5, fov: 40 };
const artifact = (kind: string, id: string, size = 100) => ({
  id, kind, size_bytes: size, generation_id: "g1", mime: "", sha256: "", metadata: {}, created_at: "", download_url: `/api/v1/artifacts/${id}/download`,
});
const generation = (over: Partial<Generation> = {}) => ({
  id: "g1", status: "COMPLETED", source: { filename: "chaise.png" },
  artifacts: [artifact("SPLAT_PLY", "ply1", PLY.length), artifact("CAMERA_JSON", "cam1"), artifact("PREVIEW_PNG", "png1")],
  ...over,
}) as unknown as Generation;
const api = (...answers: Generation[]) => {
  const generationFn = vi.fn(async () => answers.length > 1 ? answers.shift()! : answers[0]);
  return { generation: generationFn, downloadUrl: (id: string) => `/dl/${id}` } as ViewerApi & { generation: typeof generationFn };
};
const json = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));

function Scene({ bytes, camera, label, onError }: SceneProps) {
  return (
    <div data-testid="scene">
      {label} · {bytes.length} octets · {camera ? `yaw ${camera.yaw}` : "vue par défaut"}
      <button type="button" onClick={() => onError("WEBGL")}>échec webgl</button>
      <button type="button" onClick={() => onError("DECODE")}>échec décodage</button>
    </div>
  );
}

describe("ResultViewer", () => {
  it("invite à choisir une génération quand rien n'est sélectionné", () => {
    render(<ResultViewer id={null} api={api(generation())} download={vi.fn()} Scene={Scene} />);
    expect(screen.getByText(/Sélectionnez une génération terminée/)).toBeTruthy();
  });

  it("télécharge le PLY et la caméra puis affiche la scène", async () => {
    const download = vi.fn(async (url: string) => (url === "/dl/ply1" ? PLY : json(CAMERA)));
    render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    expect((await screen.findByTestId("scene")).textContent).toContain(`chaise.png · ${PLY.length} octets · yaw 0.5`);
    expect(download).toHaveBeenCalledWith("/dl/ply1", expect.objectContaining({ expectedBytes: PLY.length }));
  });

  it("affiche la progression réelle du téléchargement", async () => {
    let report: (loaded: number, total: number | null) => void = () => {};
    let finish: (bytes: Uint8Array) => void = () => {};
    const download = vi.fn((url: string, options?: { onProgress?: typeof report }) => {
      if (url !== "/dl/ply1") return Promise.resolve(json(CAMERA));
      report = options?.onProgress ?? report;
      return new Promise<Uint8Array>((resolve) => { finish = resolve; });
    });
    render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    const bar = await screen.findByRole("progressbar", { name: "Téléchargement du fichier 3D" });
    act(() => report(4_200_000, 17_000_000));
    expect(bar.getAttribute("aria-valuenow")).toBe("25");
    expect(screen.getByText("4,2 / 17,0 Mo")).toBeTruthy();
    act(() => report(5_000_000, null));
    expect(bar.getAttribute("aria-valuenow")).toBeNull();
    expect(screen.getByText("5,0 Mo")).toBeTruthy();
    await act(async () => finish(PLY));
    expect(await screen.findByTestId("scene")).toBeTruthy();
  });

  it("garde la vue par défaut quand la caméra est absente ou illisible", async () => {
    const download = vi.fn(async (url: string) => (url === "/dl/ply1" ? PLY : json({ yaw: "x" })));
    render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    expect((await screen.findByTestId("scene")).textContent).toContain("vue par défaut");
  });

  it("explique un fichier supprimé côté serveur, sans écran vide, et permet de réessayer", async () => {
    const download = vi.fn(async (url: string) => {
      if (url === "/dl/ply1") throw new ArtifactLoadError("MISSING", "absent", 404);
      return json(CAMERA);
    });
    render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/introuvable sur le serveur/);
    download.mockImplementation(async (url: string) => (url === "/dl/ply1" ? PLY : json(CAMERA)));
    await userEvent.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(await screen.findByTestId("scene")).toBeTruthy();
  });

  it("refuse un contenu qui n'est pas un PLY avant de le confier à la scène", async () => {
    const download = vi.fn(async (url: string) => (url === "/dl/ply1" ? new TextEncoder().encode("<!DOCTYPE html>") : json(CAMERA)));
    render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/pas un PLY valide/);
    expect(screen.queryByTestId("scene")).toBeNull();
  });

  it("rapporte un échec de la scène : WebGL absent ou décodage impossible", async () => {
    const download = vi.fn(async (url: string) => (url === "/dl/ply1" ? PLY : json(CAMERA)));
    const { unmount } = render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    await userEvent.click(await screen.findByRole("button", { name: "échec webgl" }));
    expect(screen.getByRole("alert").textContent).toMatch(/WebGL est indisponible/);
    unmount();
    render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    await userEvent.click(await screen.findByRole("button", { name: "échec décodage" }));
    expect(screen.getByRole("alert").textContent).toMatch(/pas un PLY valide/);
  });

  it("signale une coupure de téléchargement avec son motif", async () => {
    const download = vi.fn(async (url: string) => {
      if (url === "/dl/ply1") throw new ArtifactLoadError("TRUNCATED", "Fichier incomplet : 10 octets reçus sur 99.");
      return json(CAMERA);
    });
    render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/Fichier incomplet : 10 octets reçus sur 99\./);
  });

  it("attend la fin d'une génération en cours puis affiche son résultat", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const source = api(generation({ status: "RUNNING", artifacts: [] }), generation());
    const download = vi.fn(async (url: string) => (url === "/dl/ply1" ? PLY : json(CAMERA)));
    render(<ResultViewer id="g1" api={source} download={download} Scene={Scene} />);
    expect(await screen.findByText(/s'affichera à la fin de la génération/)).toBeTruthy();
    expect(download).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    expect(await screen.findByTestId("scene")).toBeTruthy();
  });

  it("dit qu'une génération sans fichier 3D n'a rien à montrer", async () => {
    render(<ResultViewer id="g1" api={api(generation({ status: "FAILED", artifacts: [] }))} download={vi.fn()} Scene={Scene} />);
    expect(await screen.findByText(/n'a pas produit de résultat 3D/)).toBeTruthy();
  });

  it("ne retélécharge pas le même fichier quand la génération est relue", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const download = vi.fn(async (url: string) => (url === "/dl/ply1" ? PLY : json(CAMERA)));
    const source = api(generation());
    render(<ResultViewer id="g1" api={source} download={download} Scene={Scene} />);
    await screen.findByTestId("scene");
    await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
    expect(source.generation.mock.calls.length).toBeGreaterThan(1);
    expect(download.mock.calls.filter(([url]) => url === "/dl/ply1")).toHaveLength(1);
  });

  it("abandonne le téléchargement en cours quand on change de génération", async () => {
    const signals: AbortSignal[] = [];
    const download = vi.fn((_: string, options?: { signal?: AbortSignal }) => {
      if (options?.signal) signals.push(options.signal);
      return new Promise<Uint8Array>(() => {});
    });
    const { rerender } = render(<ResultViewer id="g1" api={api(generation())} download={download} Scene={Scene} />);
    await screen.findByRole("progressbar");
    rerender(<ResultViewer id={null} api={api(generation())} download={download} Scene={Scene} />);
    expect(signals.length).toBeGreaterThan(0);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  });
});
