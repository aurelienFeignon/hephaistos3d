// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type Generation } from "@/lib/api/client";
import { GenerationActions, type ActionsApi } from "./GenerationActions";

afterEach(cleanup);

const generation = (status: Generation["status"], over: Partial<Generation> = {}) => ({ id: "g1", status, protected: false, error: null, ...over }) as Generation;
function apiOf(over: Partial<Record<keyof ActionsApi, unknown>> = {}) {
  return {
    cancel: vi.fn(async () => generation("CANCELLED")),
    retry: vi.fn(async () => generation("QUEUED")),
    protect: vi.fn(async () => generation("COMPLETED", { protected: true })),
    deleteGeneration: vi.fn(async () => undefined),
    ...over,
  } as unknown as ActionsApi & Record<keyof ActionsApi, ReturnType<typeof vi.fn>>;
}
function show(item: Generation, api = apiOf()) {
  const onChanged = vi.fn();
  const onDeleted = vi.fn();
  const view = render(<GenerationActions api={api} generation={item} onChanged={onChanged} onDeleted={onDeleted} />);
  return { api, onChanged, onDeleted, view, user: userEvent.setup() };
}
const button = (name: string | RegExp) => screen.queryByRole("button", { name });

describe("GenerationActions", () => {
  it.each([
    ["PENDING", ["Annuler", "Protéger", "Supprimer"]],
    ["QUEUED", ["Annuler", "Protéger", "Supprimer"]],
    ["RUNNING", ["Annuler", "Protéger"]],
    ["COMPLETED", ["Protéger", "Supprimer"]],
    ["FAILED", ["Relancer", "Protéger", "Supprimer"]],
    ["CANCELLED", ["Relancer", "Protéger", "Supprimer"]],
  ] as const)("propose les actions permises en %s", (status, expected) => {
    show(generation(status));
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(expected);
  });

  it("ne propose pas de relancer un import raté et dit quoi faire", () => {
    show(generation("FAILED", { error: { code: "INGEST_FAILED", message: null, needs_diagnosis: true, retryable: false } }));
    expect(button("Relancer")).toBeNull();
    expect(screen.getByText("Importez l'image de nouveau pour relancer.")).toBeTruthy();
  });

  it("annule une génération en attente et prévient", async () => {
    const { api, onChanged, user } = show(generation("QUEUED"));
    await user.click(button("Annuler") as HTMLElement);
    expect(api.cancel.mock.calls[0][0]).toBe("g1");
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  });

  it("retient qu'une annulation en cours est demandée", async () => {
    const api = apiOf({ cancel: vi.fn(async () => generation("RUNNING")) });
    const { user } = show(generation("RUNNING"), api);
    await user.click(button("Annuler") as HTMLElement);
    const pending = await screen.findByRole("button", { name: "Annulation demandée" });
    expect((pending as HTMLButtonElement).disabled).toBe(true);
  });

  it("oublie l'annulation demandée quand la génération n'est plus active", async () => {
    const api = apiOf({ cancel: vi.fn(async () => generation("RUNNING")) });
    const { user, view, onChanged, onDeleted } = show(generation("RUNNING"), api);
    await user.click(button("Annuler") as HTMLElement);
    await screen.findByRole("button", { name: "Annulation demandée" });
    view.rerender(<GenerationActions api={api} generation={generation("CANCELLED")} onChanged={onChanged} onDeleted={onDeleted} />);
    await user.click(button("Relancer") as HTMLElement);
    view.rerender(<GenerationActions api={api} generation={generation("RUNNING")} onChanged={onChanged} onDeleted={onDeleted} />);
    expect(button("Annuler")).toBeTruthy();
  });

  it("relance et prévient", async () => {
    const { api, onChanged, user } = show(generation("FAILED"));
    await user.click(button("Relancer") as HTMLElement);
    expect(api.retry.mock.calls[0][0]).toBe("g1");
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  });

  it.each([[false, true], [true, false]])("inverse la protection (%s)", async (current, next) => {
    const { api, user } = show(generation("COMPLETED", { protected: current }));
    const toggle = button("Protéger") as HTMLElement;
    expect(toggle.getAttribute("aria-pressed")).toBe(String(current));
    await user.click(toggle);
    expect(api.protect.mock.calls[0].slice(0, 2)).toEqual(["g1", next]);
  });

  it("ne supprime rien sans confirmation", async () => {
    const { api, user, onDeleted } = show(generation("COMPLETED"));
    await user.click(button("Supprimer") as HTMLElement);
    expect(api.deleteGeneration).not.toHaveBeenCalled();
    expect(screen.getByText(/définitivement/)).toBeTruthy();
    await user.click(button("Conserver") as HTMLElement);
    expect(api.deleteGeneration).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();
    expect(button("Supprimer")).toBeTruthy();
  });

  it("supprime après confirmation", async () => {
    const { api, user, onDeleted, onChanged } = show(generation("COMPLETED"));
    await user.click(button("Supprimer") as HTMLElement);
    await user.click(button("Confirmer la suppression") as HTMLElement);
    expect(api.deleteGeneration.mock.calls[0][0]).toBe("g1");
    await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("met la confirmation en garde pour une génération protégée", async () => {
    const { user } = show(generation("COMPLETED", { protected: true }));
    await user.click(button("Supprimer") as HTMLElement);
    expect(screen.getByText(/est protégée/)).toBeTruthy();
  });

  it.each([
    ["INVALID_TRANSITION", /état de cette génération a changé/],
    ["GENERATION_RUNNING", /Annulez/],
    ["GENERATION_NOT_FOUND", /n'existe plus/],
    ["UPLOAD_CAPACITY_EXCEEDED", /occupé/],
    ["AUTRE", /action a échoué/],
  ])("explique le refus %s et relit l'état", async (code, message) => {
    const api = apiOf({ retry: vi.fn(async () => { throw new ApiError(409, code); }) });
    const { user, onChanged } = show(generation("FAILED"), api);
    await user.click(button("Relancer") as HTMLElement);
    expect((await screen.findByRole("alert")).textContent).toMatch(message);
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("efface l'erreur à l'action suivante", async () => {
    const api = apiOf({ retry: vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(generation("QUEUED")) });
    const { user } = show(generation("FAILED"), api);
    await user.click(button("Relancer") as HTMLElement);
    await screen.findByRole("alert");
    await user.click(button("Relancer") as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("empêche une seconde action tant que la première n'a pas répondu", async () => {
    let finish: (value: unknown) => void = () => {};
    const api = apiOf({ retry: vi.fn(() => new Promise((resolve) => { finish = resolve; })) });
    const { user } = show(generation("FAILED"), api);
    await user.click(button("Relancer") as HTMLElement);
    expect((button("Relancer") as HTMLButtonElement).disabled).toBe(true);
    expect((button("Protéger") as HTMLButtonElement).disabled).toBe(true);
    finish(generation("QUEUED"));
    await waitFor(() => expect((button("Protéger") as HTMLButtonElement).disabled).toBe(false));
  });
});
