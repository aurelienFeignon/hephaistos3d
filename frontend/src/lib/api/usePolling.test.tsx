// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePolling } from "./usePolling";

type Data = { active: boolean };
const delayOf = (data: Data | null) => (data?.active ? 2000 : 15000);

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}
const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
const start = (load: (signal: AbortSignal) => Promise<Data>) => renderHook(() => usePolling(load, delayOf));

beforeEach(() => {
  vi.useFakeTimers();
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("usePolling", () => {
  it("charge tout de suite puis rend la donnée", async () => {
    const load = vi.fn(async () => ({ active: true }));
    const { result } = start(load);
    await advance(0);
    expect(load).toHaveBeenCalledOnce();
    expect(result.current).toMatchObject({ data: { active: true }, failed: false });
  });

  it("interroge toutes les 2 s tant que c'est actif", async () => {
    const load = vi.fn(async () => ({ active: true }));
    start(load);
    await advance(0);
    await advance(1999);
    expect(load).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("interroge toutes les 15 s sinon", async () => {
    const load = vi.fn(async () => ({ active: false }));
    start(load);
    await advance(14999);
    expect(load).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("n'attend pas la fin d'un délai long si l'activité reprend", async () => {
    const load = vi.fn().mockResolvedValueOnce({ active: true }).mockResolvedValue({ active: false });
    start(load);
    await advance(0);
    await advance(2000);
    expect(load).toHaveBeenCalledTimes(2);
    await advance(2000);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("ne fait aucune requête si l'onglet est masqué au montage", async () => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    const load = vi.fn(async () => ({ active: true }));
    start(load);
    await advance(60000);
    expect(load).not.toHaveBeenCalled();
  });

  it("s'arrête dès que l'onglet est masqué et annule la requête en cours", async () => {
    const signals: AbortSignal[] = [];
    const load = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return signals.length === 1 ? Promise.resolve({ active: true }) : new Promise<Data>(() => {});
    });
    start(load);
    await advance(2000);
    expect(load).toHaveBeenCalledTimes(2);
    setHidden(true);
    expect(signals[1].aborted).toBe(true);
    await advance(60000);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("reprend immédiatement quand l'onglet redevient visible", async () => {
    const load = vi.fn(async () => ({ active: true }));
    start(load);
    await advance(0);
    setHidden(true);
    await advance(30000);
    expect(load).toHaveBeenCalledTimes(1);
    setHidden(false);
    await advance(0);
    expect(load).toHaveBeenCalledTimes(2);
    await advance(2000);
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("garde la dernière donnée après un échec et réessaie au rythme lent", async () => {
    const load = vi.fn().mockResolvedValueOnce({ active: true }).mockRejectedValueOnce(new Error("réseau")).mockResolvedValue({ active: true });
    const { result } = start(load);
    await advance(2000);
    expect(result.current).toMatchObject({ data: { active: true }, failed: true });
    await advance(14999);
    expect(load).toHaveBeenCalledTimes(2);
    await advance(1);
    expect(load).toHaveBeenCalledTimes(3);
    expect(result.current.failed).toBe(false);
  });

  it("recharge à la demande sans doubler le rythme", async () => {
    const load = vi.fn(async () => ({ active: false }));
    const { result } = start(load);
    await advance(0);
    await advance(5000);
    act(() => result.current.refresh());
    await advance(0);
    expect(load).toHaveBeenCalledTimes(2);
    await advance(14999);
    expect(load).toHaveBeenCalledTimes(2);
    await advance(1);
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("ignore une réponse arrivée après une recharge", async () => {
    let finishFirst: (data: Data) => void = () => {};
    const load = vi.fn()
      .mockImplementationOnce(() => new Promise<Data>((resolve) => { finishFirst = resolve; }))
      .mockResolvedValue({ active: false });
    const { result } = start(load);
    act(() => result.current.refresh());
    await advance(0);
    finishFirst({ active: true });
    await advance(0);
    expect(result.current.data).toEqual({ active: false });
  });

  it("s'arrête au démontage", async () => {
    const load = vi.fn(async () => ({ active: true }));
    const { unmount } = start(load);
    await advance(0);
    unmount();
    await advance(60000);
    expect(load).toHaveBeenCalledOnce();
  });
});
