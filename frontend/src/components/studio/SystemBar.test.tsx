// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SystemBar, systemPollDelay, type SystemApi, type SystemStatus } from "./SystemBar";

beforeEach(() => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});
afterEach(cleanup);

const GIB = 1024 ** 3;
const worker = (state: string) => ({ id: state, state, waiting_reason: null, last_heartbeat_at: "", adapters: [], gpu: { name: "x", compute_capability: null, vram_free_bytes: 4 * GIB, vram_total_bytes: 12 * GIB } });
function status(over: Partial<SystemStatus> = {}): SystemStatus {
  return {
    version: "1", database: { ok: true }, storage: { free_bytes: 0, results_bytes: 0 },
    queue: { queued: 0, running: 0, gpu_slots: 1 },
    dispatch: { paused: false, paused_by: null, reason: null, since: null },
    gpu: { free_vram_bytes: 11.1 * GIB, min_free_vram_bytes: 0, vram_limit_bytes: null, waiting_reason: null },
    workers: [worker("READY")],
    ...over,
  };
}
function apiOf(over: Partial<Record<keyof SystemApi, unknown>> = {}) {
  return { status: vi.fn(async () => status()), setDispatch: vi.fn(async () => status().dispatch), ...over } as unknown as SystemApi & Record<keyof SystemApi, ReturnType<typeof vi.fn>>;
}
const paused = (paused_by: "user" | "config" | "system", reason: string | null = null) => status({ dispatch: { paused: true, paused_by, reason, since: "" } });
const show = (value: SystemStatus) => render(<SystemBar api={apiOf({ status: vi.fn(async () => value) })} />);

describe("systemPollDelay", () => {
  it("suit le contrat : 2 s avec de l'activité, 15 s sinon", () => {
    expect(systemPollDelay(status({ queue: { queued: 1, running: 0, gpu_slots: 1 } }))).toBe(2000);
    expect(systemPollDelay(status({ queue: { queued: 0, running: 1, gpu_slots: 1 } }))).toBe(2000);
    expect(systemPollDelay(status())).toBe(15000);
    expect(systemPollDelay(null)).toBe(15000);
  });
});

describe("SystemBar", () => {
  it.each([
    ["IDLE", "Worker prêt, modèle déchargé"],
    ["LOADING", "Chargement du modèle"],
    ["READY", "Modèle chargé"],
    ["BUSY", "Génération en cours"],
    ["WAITING_GPU", "En attente"],
    ["NO_GPU", "Sans GPU"],
    ["DEGRADED", "Dégradé"],
    ["ETAT_FUTUR", "ETAT_FUTUR"],
  ])("traduit l'état %s", async (state, text) => {
    show(status({ workers: [worker(state)] }));
    expect(await screen.findByText(text)).toBeTruthy();
  });

  it("dit quand aucun worker n'est actif", async () => {
    show(status({ workers: [] }));
    expect(await screen.findByText("Aucun worker actif")).toBeTruthy();
  });

  it("montre l'état le plus grave et le nombre de workers", async () => {
    show(status({ workers: [worker("READY"), worker("DEGRADED"), worker("BUSY")] }));
    expect(await screen.findByText("Dégradé (3 workers)")).toBeTruthy();
  });

  it("affiche la VRAM libre sur le total", async () => {
    show(status());
    expect(await screen.findByText("VRAM libre 11,1 / 12 Go")).toBeTruthy();
  });

  it("ne donne pas de chiffre sans mesure de VRAM", async () => {
    show(status({ workers: [], gpu: { free_vram_bytes: null, min_free_vram_bytes: 0, vram_limit_bytes: null, waiting_reason: null } }));
    expect(await screen.findByText("VRAM inconnue")).toBeTruthy();
  });

  it("affiche la VRAM libre seule si le total est inconnu", async () => {
    const w = worker("READY");
    show(status({ workers: [{ ...w, gpu: { ...w.gpu, vram_total_bytes: null } }] }));
    expect(await screen.findByText("VRAM libre 11,1 Go")).toBeTruthy();
  });

  it("n'affiche le plafond que s'il existe", async () => {
    const view = show(status());
    await screen.findByText(/VRAM libre/);
    expect(screen.queryByText(/Plafond/)).toBeNull();
    view.unmount();
    show(status({ gpu: { free_vram_bytes: GIB, min_free_vram_bytes: 0, vram_limit_bytes: 10 * GIB, waiting_reason: null } }));
    expect(await screen.findByText("Plafond 10 Go")).toBeTruthy();
  });

  it("compte la file", async () => {
    show(status({ queue: { queued: 2, running: 1, gpu_slots: 1 } }));
    expect(await screen.findByText("2 en file")).toBeTruthy();
    expect(screen.getByText("1 en cours")).toBeTruthy();
  });

  it("suspend la distribution", async () => {
    const api = apiOf();
    const onDispatchChanged = vi.fn();
    const user = userEvent.setup();
    render(<SystemBar api={api} onDispatchChanged={onDispatchChanged} />);
    await user.click(await screen.findByRole("button", { name: "Suspendre" }));
    expect(api.setDispatch.mock.calls[0][0]).toEqual({ paused: true });
    await waitFor(() => expect(onDispatchChanged).toHaveBeenCalledOnce());
    await waitFor(() => expect(api.status.mock.calls.length).toBeGreaterThan(1));
  });

  it("reprend la distribution et dit qu'elle est suspendue", async () => {
    const api = apiOf({ status: vi.fn(async () => paused("user")) });
    const user = userEvent.setup();
    render(<SystemBar api={api} />);
    expect(await screen.findByText("Distribution suspendue")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Reprendre" }));
    expect(api.setDispatch.mock.calls[0][0]).toEqual({ paused: false });
  });

  it("distingue une suspension par le système et donne sa raison", async () => {
    show(paused("system", "3 pannes GPU"));
    expect(await screen.findByText("Distribution suspendue par le système : 3 pannes GPU")).toBeTruthy();
  });

  it("explique un échec sans changer d'état", async () => {
    const api = apiOf({ setDispatch: vi.fn(async () => { throw new Error("x"); }) });
    const onDispatchChanged = vi.fn();
    const user = userEvent.setup();
    render(<SystemBar api={api} onDispatchChanged={onDispatchChanged} />);
    await user.click(await screen.findByRole("button", { name: "Suspendre" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/n'a pas pu être modifiée/);
    expect(onDispatchChanged).not.toHaveBeenCalled();
  });

  it("empêche un double clic pendant l'envoi", async () => {
    let finish: (value: unknown) => void = () => {};
    const api = apiOf({ setDispatch: vi.fn(() => new Promise((resolve) => { finish = resolve; })) });
    const user = userEvent.setup();
    render(<SystemBar api={api} />);
    const button = await screen.findByRole("button", { name: "Suspendre" });
    await user.click(button);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    finish(status().dispatch);
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  });

  it("ne montre pas de valeurs périmées si le service ne répond plus", async () => {
    const load = vi.fn().mockResolvedValueOnce(status()).mockRejectedValue(new Error("x"));
    const api = apiOf({ status: load });
    const view = render(<SystemBar api={api} />);
    await screen.findByText(/VRAM libre/);
    view.rerender(<SystemBar api={api} refreshKey={1} />);
    expect((await screen.findByRole("status")).textContent).toMatch(/ne répond pas/);
    expect(screen.queryByText(/VRAM libre/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Suspendre" })).toBeNull();
  });

  it("annonce le chargement initial", () => {
    render(<SystemBar api={apiOf({ status: vi.fn(() => new Promise(() => {})) })} />);
    expect(screen.getByText("Lecture de l'état du système…")).toBeTruthy();
  });
});
