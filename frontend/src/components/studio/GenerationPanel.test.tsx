// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import type { DecodedImage } from "@/lib/image/inspect";
import { GenerationPanel, type GenerationApi } from "./GenerationPanel";

afterEach(cleanup);

const model = (id: string, name: string, available = true) => ({
  id, name, available, version: "1", capabilities: [], input_formats: [], output_kinds: [], gpu: null, default_preset: "b",
  unavailable_reason: available ? null : "No active worker for this model",
});
const schemaOf = (title: string) => ({
  type: "object",
  additionalProperties: false,
  properties: {
    genre: { type: "string", title: "Genre", enum: ["x", "y"], default: "x", "x-ui": { widget: "radio", order: 1, labels: { x: "Ixe", y: "Igrec" } } },
    pas: { type: "integer", title, default: 10, minimum: 1, maximum: 20, "x-ui": { widget: "number", order: 2 } },
  },
});
const presets = { a: { label: "Vif", values: { pas: 5 } }, b: { label: "Moyen", values: { pas: 10 } } };

function apiOf(over: Partial<GenerationApi> = {}) {
  return {
    models: vi.fn(async () => [model("m1", "Alpha"), model("m2", "Beta", false)]),
    modelSchema: vi.fn(async (id: string) => schemaOf(id === "m1" ? "Pas" : "Tours")),
    presets: vi.fn(async () => presets),
    createGeneration: vi.fn(async () => ({ id: "g1", status: "QUEUED" })),
    ...over,
  } as unknown as GenerationApi & { createGeneration: ReturnType<typeof vi.fn> };
}

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13];
const png = () => new File([new Uint8Array(PNG)], "objet.png");
const decode = async (): Promise<DecodedImage> => ({ url: "blob:x", width: 10, height: 10, hasTransparency: () => false, release: () => {} });
const submit = () => screen.findByRole("button", { name: "Générer" });

async function ready(api = apiOf()) {
  const user = userEvent.setup({ applyAccept: false });
  render(<GenerationPanel api={api} decode={decode} />);
  await screen.findByRole("radio", { name: "Moyen" });
  return { user, api };
}
async function withImage(api = apiOf()) {
  const ctx = await ready(api);
  await ctx.user.upload(screen.getByLabelText(/Importer une image/), png());
  await screen.findByRole("img", { name: "Aperçu de objet.png" });
  return ctx;
}
const sent = (api: { createGeneration: ReturnType<typeof vi.fn> }) => api.createGeneration.mock.calls[0][0] as FormData;

describe("GenerationPanel", () => {
  beforeEach(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  });

  it("actualise la disponibilité sans changer de modèle ni perdre la saisie", async () => {
    const models = vi.fn().mockResolvedValueOnce([model("m1", "Alpha"), model("m2", "Beta")]).mockResolvedValue([model("m1", "Alpha", false), model("m2", "Beta")]);
    const api = apiOf({ models } as never);
    const user = userEvent.setup();
    const view = render(<GenerationPanel api={api} decode={decode} />);
    const pas = await screen.findByRole("spinbutton", { name: "Pas" });
    await user.clear(pas);
    await user.type(pas, "7");
    view.rerender(<GenerationPanel api={api} decode={decode} refreshKey={1} />);
    expect(await screen.findByRole("option", { name: "Alpha (indisponible)" })).toBeTruthy();
    expect((screen.getByRole("combobox", { name: "Modèle" }) as HTMLSelectElement).value).toBe("m1");
    expect((screen.getByRole("spinbutton", { name: "Pas" }) as HTMLInputElement).value).toBe("7");
  });

  it("sélectionne le premier modèle disponible et son préréglage par défaut", async () => {
    await ready();
    expect((screen.getByRole("combobox", { name: "Modèle" }) as HTMLSelectElement).value).toBe("m1");
    expect((screen.getByRole("radio", { name: "Moyen" }) as HTMLInputElement).checked).toBe(true);
  });

  it("montre un modèle indisponible avec sa raison, sans empêcher d'attendre", async () => {
    const { user } = await ready();
    expect(screen.getByRole("option", { name: "Beta (indisponible)" })).toBeTruthy();
    await user.selectOptions(screen.getByRole("combobox", { name: "Modèle" }), "m2");
    expect((await screen.findByText(/No active worker for this model/)).textContent).toMatch(/attendra/);
    expect(await screen.findByRole("spinbutton", { name: "Tours" })).toBeTruthy();
  });

  it("affiche les valeurs du préréglage choisi", async () => {
    const { user } = await ready();
    expect(screen.getByTestId("preset-values").textContent).toBe("Pas10");
    await user.click(screen.getByRole("radio", { name: "Vif" }));
    expect(screen.getByTestId("preset-values").textContent).toBe("Pas5");
    expect((screen.getByRole("spinbutton", { name: "Pas" }) as HTMLInputElement).value).toBe("5");
  });

  it("garde Générer désactivé tant qu'il n'y a pas d'image", async () => {
    await ready();
    expect((await submit() as HTMLButtonElement).disabled).toBe(true);
  });

  it("envoie l'image, le préréglage et seulement ce qui a changé", async () => {
    const { user, api } = await withImage();
    await user.click(screen.getByRole("radio", { name: "Igrec" }));
    await user.click(await submit());
    await screen.findByText("Génération ajoutée à la file.");
    const body = sent(api);
    expect((body.get("image") as File).name).toBe("objet.png");
    expect(body.get("model_id")).toBe("m1");
    expect(body.get("preset")).toBe("b");
    expect(JSON.parse(body.get("params") as string)).toEqual({ genre: "y" });
    expect(body.get("seed")).toBe("random");
  });

  it("prévient la file dès qu'une génération est créée, pas après un refus", async () => {
    const onCreated = vi.fn();
    const user = userEvent.setup({ applyAccept: false });
    const create = vi.fn().mockResolvedValueOnce({ id: "g", status: "QUEUED" }).mockRejectedValueOnce(new ApiError(500, "X"));
    render(<GenerationPanel api={apiOf({ createGeneration: create } as never)} decode={decode} onCreated={onCreated} />);
    await screen.findByRole("radio", { name: "Moyen" });
    await user.upload(screen.getByLabelText(/Importer une image/), png());
    await screen.findByRole("img", { name: "Aperçu de objet.png" });
    await user.click(await submit());
    await screen.findByText("Génération ajoutée à la file.");
    expect(onCreated).toHaveBeenCalledOnce();
    await user.click(await submit());
    await screen.findByRole("alert");
    expect(onCreated).toHaveBeenCalledOnce();
  });

  it("transmet une graine choisie", async () => {
    const { user, api } = await withImage();
    await user.type(screen.getByRole("textbox", { name: "Graine" }), "42");
    await user.click(await submit());
    await waitFor(() => expect(api.createGeneration).toHaveBeenCalledOnce());
    expect(sent(api).get("seed")).toBe("42");
  });

  it("refuse côté client une valeur hors bornes, sans appeler l'API", async () => {
    const { user, api } = await withImage();
    const pas = screen.getByRole("spinbutton", { name: "Pas" });
    await user.clear(pas);
    await user.type(pas, "21");
    await user.click(await submit());
    expect(await screen.findByText("Entrez une valeur d'au plus 20.")).toBeTruthy();
    expect(api.createGeneration).not.toHaveBeenCalled();
  });

  it("refuse une graine invalide", async () => {
    const { user, api } = await withImage();
    await user.type(screen.getByRole("textbox", { name: "Graine" }), "abc");
    await user.click(await submit());
    expect(await screen.findByText(/laissez vide/)).toBeTruthy();
    expect(api.createGeneration).not.toHaveBeenCalled();
  });

  it.each([
    ["INSUFFICIENT_VRAM_BUDGET", 422, /mémoire graphique/],
    ["PARAMS_INVALID", 422, /réglages/],
    ["UPLOAD_CAPACITY_EXCEEDED", 429, /occupé/],
    ["AUTRE_CODE", 500, /n'a pas pu être créée/],
  ])("traduit le refus %s", async (code, status, message) => {
    const api = apiOf({ createGeneration: vi.fn(async () => { throw new ApiError(status as number, code as string); }) as never });
    const { user } = await withImage(api);
    await user.click(await submit());
    expect((await screen.findByRole("alert")).textContent).toMatch(message as RegExp);
  });

  it("signale une génération créée mais déjà en échec", async () => {
    const api = apiOf({ createGeneration: vi.fn(async () => ({ id: "g", status: "FAILED" })) as never });
    const { user } = await withImage(api);
    await user.click(await submit());
    expect((await screen.findByRole("alert")).textContent).toMatch(/n'a pas pu être enregistrée/);
  });

  it("empêche un double envoi pendant la création", async () => {
    let finish: (value: unknown) => void = () => {};
    const api = apiOf({ createGeneration: vi.fn(() => new Promise((resolve) => { finish = resolve; })) as never });
    const { user } = await withImage(api);
    const button = await submit();
    await user.click(button);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    finish({ id: "g", status: "QUEUED" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    expect(api.createGeneration).toHaveBeenCalledOnce();
  });

  it("garde l'image en changeant de modèle", async () => {
    const { user } = await withImage();
    await user.selectOptions(screen.getByRole("combobox", { name: "Modèle" }), "m2");
    await screen.findByRole("spinbutton", { name: "Tours" });
    expect(screen.getByRole("img", { name: "Aperçu de objet.png" })).toBeTruthy();
    expect(((await submit()) as HTMLButtonElement).disabled).toBe(false);
  });

  it("propose de réessayer si les modèles ne chargent pas", async () => {
    const models = vi.fn().mockRejectedValueOnce(new Error("réseau")).mockResolvedValue([model("m1", "Alpha")]);
    const user = userEvent.setup();
    render(<GenerationPanel api={apiOf({ models } as never)} decode={decode} />);
    await user.click(await screen.findByRole("button", { name: "Réessayer" }));
    expect(await screen.findByRole("radio", { name: "Moyen" })).toBeTruthy();
  });

  it("signale un schéma que le formulaire ne sait pas rendre", async () => {
    render(<GenerationPanel api={apiOf({ modelSchema: vi.fn(async () => ({ type: "object", properties: { a: { type: "array" } } })) } as never)} decode={decode} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/réglages de ce modèle/);
  });
});
