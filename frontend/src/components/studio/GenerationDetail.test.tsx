// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Generation } from "@/lib/api/client";
import { GenerationDetail, type DetailApi } from "./GenerationDetail";

beforeEach(() => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});
afterEach(cleanup);

const generation = (over: Partial<Generation> = {}) => ({
  id: "g1", status: "COMPLETED", model_id: "idol", model_version: "sdk 1", preset: "balanced", seed: 42, elapsed_ms: 43200, attempt_count: 1, max_retries: 3, protected: false, error: null,
  params: { mode: "real", steps: 50, orphelin: 7 },
  progress: { stage: null, current: null, total: null, updated_at: null },
  source: { filename: "chaise.png", url: "/api/v1/generations/g1/source", width: 768, height: 512, has_alpha: true },
  artifacts: [],
  auto_delete_after: null,
  ...over,
}) as Generation;
const artifact = (id: string, kind: string, size_bytes: number) => ({
  id, kind, size_bytes, generation_id: "g1", mime: "application/octet-stream", sha256: "0".repeat(64), metadata: {}, created_at: "", download_url: `/api/v1/artifacts/${id}/download`,
});
const finished = () => generation({ artifacts: [artifact("c", "CAMERA_JSON", 80), artifact("p", "SPLAT_PLY", 17_043_456), artifact("v", "PREVIEW_PNG", 120_000)] } as Partial<Generation>);
const schema = {
  type: "object", additionalProperties: false,
  properties: {
    mode: { type: "string", title: "Type d'image", enum: ["synthetic", "real"], default: "synthetic", "x-ui": { widget: "radio", order: 1, labels: { real: "Photo" } } },
    steps: { type: "integer", title: "Pas", default: 10, "x-ui": { widget: "number", order: 2 } },
  },
};
const attempt = (number: number, over = {}) => ({
  id: `a${number}`, number, outcome: "SUCCEEDED", trigger: "INITIAL", error_code: null, error_message: null, retryable: null,
  stage_durations_ms: { sampling: 42400, preprocess: 200 }, started_at: "", finished_at: "", generation_id: "g1", worker_id: "w", free_vram_at_start_bytes: null, peak_vram_bytes: null, ...over,
});
function apiOf(over: Partial<Record<keyof DetailApi, unknown>> = {}) {
  return {
    generation: vi.fn(async () => generation()),
    attempts: vi.fn(async () => ({ items: [attempt(1)], next_after: null })),
    modelSchema: vi.fn(async () => schema),
    protect: vi.fn(async () => generation({ protected: true })),
    deleteGeneration: vi.fn(async () => undefined),
    logs: vi.fn(async () => [{ timestamp: "2026-11-09T14:00:05Z", event: "stage_started", stage: "sampling", generation_id: "g1" }]),
    ...over,
  } as unknown as DetailApi & { logs: ReturnType<typeof vi.fn> };
}
const show = async (api = apiOf(), onClose = () => {}) => {
  render(<GenerationDetail api={api} id="g1" onClose={onClose} />);
  await screen.findByText("chaise.png");
  return userEvent.setup();
};
const section = (name: string) => screen.getByRole("region", { name });
const loaded = (name: string) => screen.findByRole("region", { name });
const pairs = (root: HTMLElement) => [...root.querySelectorAll("dt")].map((dt) => `${dt.textContent}=${dt.nextElementSibling?.textContent}`);

describe("GenerationDetail", () => {
  it("présente l'image source et son statut", async () => {
    await show();
    expect(screen.getByText("Terminé")).toBeTruthy();
    expect(screen.getByText("768 × 512")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Image d'entrée" }).getAttribute("src")).toBe("/api/v1/generations/g1/source");
  });

  it("nomme les paramètres depuis le schéma et garde ceux qu'il ne connaît pas", async () => {
    await show();
    expect(await within(section("Paramètres effectifs")).findByText("Type d'image")).toBeTruthy();
    expect(pairs(section("Paramètres effectifs"))).toEqual(expect.arrayContaining(["Type d'image=Photo", "Pas=50", "orphelin=7", "Préréglage=balanced", "Graine=42", "Modèle=idol (sdk 1)"]));
  });

  it("affiche les noms bruts si le schéma est indisponible", async () => {
    await show(apiOf({ modelSchema: vi.fn(async () => { throw new Error("réseau"); }) }));
    expect(await within(section("Paramètres effectifs")).findByText("mode")).toBeTruthy();
    expect(pairs(section("Paramètres effectifs"))).toContain("mode=real");
  });

  it("propose le PLY, l'aperçu et la caméra au nom construit par le serveur (P4.4)", async () => {
    await show(apiOf({ generation: vi.fn(async () => finished()) }));
    const links = within(section("Télécharger")).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("aria-label"))).toEqual(["Télécharger splat.ply (17,0 Mo)", "Télécharger l'aperçu PNG", "Télécharger camera.json"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/api/v1/artifacts/p/download", "/api/v1/artifacts/v/download", "/api/v1/artifacts/c/download"]);
    // Sans valeur, l'attribut laisse au navigateur le nom de Content-Disposition.
    expect(links.map((link) => link.getAttribute("download"))).toEqual(["", "", ""]);
  });

  it("ne propose que les fichiers présents", async () => {
    await show(apiOf({ generation: vi.fn(async () => generation({ artifacts: [artifact("p", "SPLAT_PLY", 1_500_000)] } as Partial<Generation>)) }));
    expect(within(section("Télécharger")).getAllByRole("link").map((link) => link.textContent)).toEqual(["splat.ply1,5 Mo"]);
  });

  it("n'offre aucun téléchargement tant que la génération n'est pas terminée", async () => {
    await show(apiOf({ generation: vi.fn(async () => ({ ...finished(), status: "RUNNING" }) as Generation) }));
    expect(screen.queryByRole("region", { name: "Télécharger" })).toBeNull();
  });

  it("annonce la date de suppression automatique (P4.8)", async () => {
    await show(apiOf({ generation: vi.fn(async () => generation({ auto_delete_after: "2026-12-11T12:00:00Z" })) }));
    const text = section("Conservation").textContent ?? "";
    expect(text).toMatch(/Supprimée automatiquement après le 11 décembre 2026/);
    expect(text).toContain("Protégez-la pour la conserver.");
  });

  it("signale une suppression imminente", async () => {
    vi.useFakeTimers({ toFake: ["Date"], now: new Date("2026-12-09T12:00:00Z") });
    try {
      await show(apiOf({ generation: vi.fn(async () => generation({ auto_delete_after: "2026-12-11T12:00:00Z" })) }));
      expect(within(section("Conservation")).getByText(/Supprimée automatiquement/).className).toContain("text-danger");
    } finally {
      vi.useRealTimers();
    }
  });

  it("dit qu'une génération protégée n'est jamais supprimée automatiquement", async () => {
    await show(apiOf({ generation: vi.fn(async () => generation({ protected: true })) }));
    expect(section("Conservation").textContent).toBe("ConservationProtégée : jamais supprimée automatiquement.");
  });

  it("dit quand la rétention est désactivée", async () => {
    await show();
    expect(section("Conservation").textContent).toContain("Conservée sans limite : la suppression automatique est désactivée.");
  });

  it("ne parle pas de conservation avant la fin", async () => {
    await show(apiOf({ generation: vi.fn(async () => generation({ status: "QUEUED" })) }));
    expect(screen.queryByRole("region", { name: "Conservation" })).toBeNull();
  });

  it("n'affiche pas de section Erreur sans erreur", async () => {
    await show();
    expect(screen.queryByRole("region", { name: "Erreur" })).toBeNull();
  });

  it("détaille l'erreur et le besoin de diagnostic", async () => {
    await show(apiOf({ generation: vi.fn(async () => generation({ status: "FAILED", error: { code: "INTERNAL", message: "boom", needs_diagnosis: true, retryable: false } })) }));
    const region = section("Erreur");
    expect(within(region).getByText("INTERNAL")).toBeTruthy();
    expect(within(region).getByText("boom")).toBeTruthy();
    expect(within(region).getByText(/diagnostic/)).toBeTruthy();
  });

  it("donne les durées par étape de la dernière tentative, dans l'ordre d'exécution", async () => {
    await show(apiOf({ attempts: vi.fn(async () => ({ items: [attempt(1, { stage_durations_ms: { encode: 1 } }), attempt(2)], next_after: null })) }));
    await within(await loaded("Durées par étape")).findByText("Échantillonnage");
    expect(pairs(section("Durées par étape"))).toEqual(["Prétraitement=0,2 s", "Échantillonnage=42,4 s"]);
  });

  it("liste les tentatives avec leur issue et leur erreur", async () => {
    await show(apiOf({ attempts: vi.fn(async () => ({ items: [attempt(1, { outcome: "FAILED", error_code: "GPU_OOM", error_message: "plus de mémoire" }), attempt(2, { outcome: null })], next_after: null })) }));
    const region = await loaded("Tentatives");
    expect(await within(region).findByText("Tentative 1 : échec")).toBeTruthy();
    expect(within(region).getByText("GPU_OOM : plus de mémoire")).toBeTruthy();
    expect(within(region).getByText("Tentative 2 : en cours")).toBeTruthy();
  });

  it("traduit l'issue réussie publiée par l'API", async () => {
    await show();
    expect(await within(await loaded("Tentatives")).findByText("Tentative 1 : réussie")).toBeTruthy();
  });

  it("charge le journal d'une tentative à la demande", async () => {
    const api = apiOf();
    const user = await show(api);
    expect(api.logs).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "Voir le journal de la tentative 1" }));
    expect((await screen.findByText(/stage_started/)).textContent).toContain('"stage":"sampling"');
    expect(api.logs.mock.calls[0].slice(0, 2)).toEqual(["g1", { attempt: 1, tail: 200 }]);
  });

  it("dit quand un journal est vide ou illisible", async () => {
    const logs = vi.fn().mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("x"));
    const user = await show(apiOf({ logs, attempts: vi.fn(async () => ({ items: [attempt(1), attempt(2)], next_after: null })) }));
    await user.click(await screen.findByRole("button", { name: "Voir le journal de la tentative 1" }));
    expect(await screen.findByText("Ce journal est vide.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Voir le journal de la tentative 2" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/journal/);
  });

  it("relit la génération après une action et prévient le Studio", async () => {
    const api = apiOf();
    const onChanged = vi.fn();
    render(<GenerationDetail api={api} id="g1" onClose={() => {}} onChanged={onChanged} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Protéger" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    await waitFor(() => expect((api.generation as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(1));
  });

  it("prévient le Studio d'une suppression", async () => {
    const onDeleted = vi.fn();
    render(<GenerationDetail api={apiOf()} id="g1" onClose={() => {}} onDeleted={onDeleted} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Supprimer" }));
    await user.click(screen.getByRole("button", { name: "Confirmer la suppression" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  });

  it("permet de revenir au formulaire", async () => {
    const onClose = vi.fn();
    const user = await show(apiOf(), onClose);
    await user.click(screen.getByRole("button", { name: "Créer une génération" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("signale un chargement impossible", async () => {
    render(<GenerationDetail api={apiOf({ generation: vi.fn(async () => { throw new Error("x"); }) })} id="g1" onClose={() => {}} />);
    expect((await screen.findByRole("status")).textContent).toMatch(/Impossible de charger/);
  });
});
