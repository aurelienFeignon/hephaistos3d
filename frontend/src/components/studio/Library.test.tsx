// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Generation } from "@/lib/api/client";
import { Library, type LibraryApi } from "./Library";

beforeEach(() => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});
afterEach(cleanup);

function generation(id: string, over: Partial<Generation> = {}): Generation {
  return {
    id, status: "COMPLETED", model_id: "idol", elapsed_ms: 43000, attempt_count: 1, protected: false, error: null,
    progress: { stage: null, current: null, total: null, updated_at: null },
    source: { filename: `${id}.png`, url: `/api/v1/generations/${id}/source` },
    ...over,
  } as Generation;
}
const page = (items: Generation[], next_cursor: string | null = null) => ({ items, next_cursor });
function apiOf(generations: (...args: never[]) => Promise<unknown>) {
  return { generations: vi.fn(generations) } as unknown as LibraryApi & { generations: ReturnType<typeof vi.fn> };
}
const show = (api: LibraryApi, props: Partial<Parameters<typeof Library>[0]> = {}) =>
  render(<Library api={api} selectedId={null} onSelect={() => {}} hasActive={false} {...props} />);
const filter = () => screen.getByRole("combobox", { name: "Statut" });

describe("Library", () => {
  it("liste les générations avec statut, modèle et durée", async () => {
    show(apiOf(async () => page([generation("chaise")])));
    const row = await screen.findByRole("button", { name: /chaise\.png/ });
    expect(within(row).getByText("Terminé")).toBeTruthy();
    expect(within(row).getByText("idol · 0:43")).toBeTruthy();
    expect(row.querySelector("img")?.getAttribute("src")).toBe("/api/v1/generations/chaise/source");
  });

  it("montre le pas réel d'une génération en cours", async () => {
    show(apiOf(async () => page([generation("a", { status: "RUNNING", progress: { stage: "sampling", current: 12, total: 30, updated_at: null } })])));
    expect(await screen.findByText("12/30")).toBeTruthy();
  });

  it("montre le code d'erreur et les tentatives d'un échec", async () => {
    show(apiOf(async () => page([generation("a", { status: "FAILED", attempt_count: 2, error: { code: "GPU_OOM", message: null, needs_diagnosis: false, retryable: true } })])));
    expect(await screen.findByText("GPU_OOM · 2 tentatives")).toBeTruthy();
    expect(screen.getByText("Échoué")).toBeTruthy();
  });

  it("signale une génération protégée", async () => {
    show(apiOf(async () => page([generation("a", { protected: true })])));
    expect(await screen.findByText("idol · 0:43 · protégée")).toBeTruthy();
  });

  it("signale la génération sélectionnée et remonte le choix", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    show(apiOf(async () => page([generation("a"), generation("b")])), { selectedId: "b", onSelect });
    const a = await screen.findByRole("button", { name: /a\.png/ });
    expect(a.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("button", { name: /b\.png/ }).getAttribute("aria-pressed")).toBe("true");
    await user.click(a);
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it.each([
    ["En cours", { status: ["QUEUED", "RUNNING"] }],
    ["Terminées", { status: ["COMPLETED"] }],
    ["Échouées", { status: ["FAILED"] }],
    ["Annulées", { status: ["CANCELLED"] }],
    ["Protégées", { protected: true }],
  ])("filtre sur %s", async (label, query) => {
    const api = apiOf(async () => page([]));
    const user = userEvent.setup();
    show(api);
    await screen.findByText(/Aucune génération/);
    await user.selectOptions(filter(), label);
    await waitFor(() => expect(api.generations.mock.calls.at(-1)?.[0]).toEqual({ limit: 30, ...query }));
  });

  it("agrandit la lecture pour afficher plus, puis retire le bouton", async () => {
    const generations = vi.fn(async (query: { limit: number }) =>
      query.limit === 30 ? page([generation("a"), generation("b")], "c1") : page([generation("a"), generation("b"), generation("c")]));
    const user = userEvent.setup();
    show({ generations } as unknown as LibraryApi);
    await user.click(await screen.findByRole("button", { name: "Afficher plus" }));
    await screen.findByRole("button", { name: /c\.png/ });
    expect(generations.mock.calls.at(-1)?.[0]).toEqual({ limit: 60 });
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "Afficher plus" })).toBeNull();
  });

  it("garde à jour les générations des pages déjà affichées", async () => {
    let status: Generation["status"] = "QUEUED";
    const generations = vi.fn(async (query: { limit: number }) =>
      query.limit === 30 ? page([generation("a")], "c1") : page([generation("a"), generation("ancienne", { status })]));
    const api = { generations } as unknown as LibraryApi;
    const user = userEvent.setup();
    const view = show(api);
    await user.click(await screen.findByRole("button", { name: "Afficher plus" }));
    await screen.findByText("En file");
    status = "COMPLETED";
    view.rerender(<Library api={api} selectedId={null} onSelect={() => {}} hasActive />);
    const row = await screen.findByRole("button", { name: /ancienne/ });
    await waitFor(() => expect(within(row).getByText("Terminé")).toBeTruthy());
  });

  it("repart de la première page quand le filtre change", async () => {
    const generations = vi.fn(async (query: { limit: number; protected?: boolean }) =>
      query.protected ? page([generation("p")]) : query.limit === 30 ? page([generation("a")], "c1") : page([generation("a"), generation("b")]));
    const user = userEvent.setup();
    show({ generations } as unknown as LibraryApi);
    await user.click(await screen.findByRole("button", { name: "Afficher plus" }));
    await screen.findByRole("button", { name: /b\.png/ });
    await user.selectOptions(filter(), "Protégées");
    await screen.findByRole("button", { name: /p\.png/ });
    expect(generations.mock.calls.at(-1)?.[0]).toEqual({ limit: 30, protected: true });
    expect(screen.queryByRole("button", { name: /b\.png/ })).toBeNull();
  });

  // Au-delà du maximum de l'API (100), la suite s'ajoute par curseur.
  async function clickUntilCursor(generations: ReturnType<typeof vi.fn>) {
    const user = userEvent.setup();
    for (const limit of [60, 90, 100]) {
      await user.click(await screen.findByRole("button", { name: "Afficher plus" }));
      await waitFor(() => expect(generations.mock.calls.at(-1)?.[0]).toEqual({ limit }));
    }
    await waitFor(() => expect((screen.getByRole("button", { name: "Afficher plus" }) as HTMLButtonElement).disabled).toBe(false));
    await user.click(screen.getByRole("button", { name: "Afficher plus" }));
  }

  it("ne dépasse pas le maximum de l'API et continue par curseur, sans doublon", async () => {
    const generations = vi.fn(async (query: { cursor?: string }) =>
      query.cursor ? page([generation("a"), generation("z")]) : page([generation("a")], "c1"));
    show({ generations } as unknown as LibraryApi);
    await clickUntilCursor(generations);
    await screen.findByRole("button", { name: /z\.png/ });
    expect(generations.mock.calls.at(-1)?.[0]).toEqual({ limit: 30, cursor: "c1" });
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("dit si la suite échoue, sans perdre la liste", async () => {
    const generations = vi.fn(async (query: { cursor?: string }) => {
      if (query.cursor) throw new Error("réseau");
      return page([generation("a")], "c1");
    });
    show({ generations } as unknown as LibraryApi);
    await clickUntilCursor(generations);
    expect((await screen.findByRole("alert")).textContent).toMatch(/suite/);
    expect(screen.getByRole("button", { name: /a\.png/ })).toBeTruthy();
  });

  it("relit la liste après une action et oublie les pages ajoutées par curseur", async () => {
    const generations = vi.fn(async (query: { cursor?: string }) =>
      query.cursor ? page([generation("z")]) : page([generation("a")], "c1"));
    const api = { generations } as unknown as LibraryApi;
    const view = show(api);
    await clickUntilCursor(generations);
    await screen.findByRole("button", { name: /z\.png/ });
    const calls = generations.mock.calls.length;
    view.rerender(<Library api={api} selectedId={null} onSelect={() => {}} hasActive={false} refreshKey={1} />);
    await waitFor(() => expect(generations.mock.calls.length).toBeGreaterThan(calls));
    await waitFor(() => expect(screen.queryByRole("button", { name: /z\.png/ })).toBeNull());
  });

  it("distingue le chargement, le vide et l'échec", async () => {
    let finish: (value: unknown) => void = () => {};
    const view = show(apiOf(() => new Promise((resolve) => { finish = resolve; })));
    expect(screen.getByText("Chargement de la bibliothèque…")).toBeTruthy();
    finish(page([]));
    expect(await screen.findByText("Aucune génération pour ce filtre.")).toBeTruthy();
    view.unmount();
    show(apiOf(async () => { throw new Error("réseau"); }));
    expect((await screen.findByRole("status")).textContent).toMatch(/ne répond pas/);
    expect(screen.queryByText(/Aucune génération/)).toBeNull();
  });

  it("relit la liste dès que l'activité change", async () => {
    const api = apiOf(async () => page([generation("a")]));
    const view = show(api);
    await screen.findByRole("button", { name: /a\.png/ });
    expect(api.generations).toHaveBeenCalledTimes(1);
    view.rerender(<Library api={api} selectedId={null} onSelect={() => {}} hasActive />);
    await waitFor(() => expect(api.generations).toHaveBeenCalledTimes(2));
  });
});
