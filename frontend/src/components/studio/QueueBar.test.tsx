// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { QueueItem } from "@/lib/api/client";
import { QueueBar, queuePollDelay } from "./QueueBar";

afterEach(cleanup);

function item(over: Partial<QueueItem>): QueueItem {
  return {
    id: "g", status: "QUEUED", position: 1, waiting_reason: null, elapsed_ms: null,
    progress: { stage: null, current: null, total: null, updated_at: null },
    source: { filename: "lampe.png" },
    ...over,
  } as QueueItem;
}
const running = (progress: Partial<QueueItem["progress"]>, elapsed_ms: number | null = 31000) =>
  item({ status: "RUNNING", position: null, elapsed_ms, progress: { stage: null, current: null, total: null, updated_at: null, ...progress } });

describe("queuePollDelay", () => {
  it("suit le contrat : 2 s avec une génération active, 15 s sinon", () => {
    expect(queuePollDelay({ items: [item({})], next_cursor: null })).toBe(2000);
    expect(queuePollDelay({ items: [], next_cursor: null })).toBe(15000);
    expect(queuePollDelay(null)).toBe(15000);
  });
});

describe("QueueBar", () => {
  it("annonce le chargement sans prétendre que la file est vide", () => {
    render(<QueueBar items={null} failed={false} />);
    expect(screen.getByText("Chargement de la file…")).toBeTruthy();
    expect(screen.queryByText(/Aucune génération/)).toBeNull();
  });

  it("dit quand la file est vide", () => {
    render(<QueueBar items={[]} failed={false} />);
    expect(screen.getByText("Aucune génération en cours ni en attente.")).toBeTruthy();
  });

  it("montre l'étape, le pas réel et la durée", () => {
    render(<QueueBar items={[running({ stage: "sampling", current: 23, total: 50 })]} failed={false} />);
    expect(screen.getByText("lampe.png")).toBeTruthy();
    expect(screen.getByText("Échantillonnage 23/50")).toBeTruthy();
    expect(screen.getByText("0:31")).toBeTruthy();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("23");
    expect(bar.getAttribute("aria-valuemax")).toBe("50");
  });

  it("n'invente aucune progression sans pas connu", () => {
    render(<QueueBar items={[running({ stage: "encode" })]} failed={false} />);
    expect(screen.getByText("Encodage")).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("indique le démarrage avant la première étape", () => {
    render(<QueueBar items={[running({}, null)]} failed={false} />);
    expect(screen.getByText("Démarrage")).toBeTruthy();
  });

  it.each([
    ["VRAM_INSUFFICIENT", "attend de la mémoire graphique"],
    ["DISPATCH_PAUSED", "distribution suspendue"],
    ["NO_WORKER", "aucun worker actif"],
    ["RETRY_BACKOFF", "relance programmée"],
    [null, "en attente"],
    ["MOTIF_FUTUR", "en attente"],
  ])("traduit le motif d'attente %s", (reason, text) => {
    render(<QueueBar items={[item({ position: 2, waiting_reason: reason })]} failed={false} />);
    expect(screen.getByText("#2")).toBeTruthy();
    expect(screen.getByText(text)).toBeTruthy();
  });

  it("conserve l'ordre de l'API", () => {
    render(<QueueBar items={[running({ stage: "decode" }), item({ id: "b", position: 1, source: { filename: "renard.png" } as QueueItem["source"] })]} failed={false} />);
    expect(screen.getAllByRole("listitem").map((li) => li.textContent?.includes("renard"))).toEqual([false, true]);
  });

  it("garde la file connue quand l'interrogation échoue", () => {
    render(<QueueBar items={[item({})]} failed />);
    expect(screen.getByText("lampe.png")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/ne répond pas/);
  });

  it("n'affirme rien sur la file si le premier chargement échoue", () => {
    render(<QueueBar items={null} failed />);
    expect(screen.getByRole("status").textContent).toMatch(/ne répond pas/);
    expect(screen.queryByText(/Chargement|Aucune/)).toBeNull();
  });
});
