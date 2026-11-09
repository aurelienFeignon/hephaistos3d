// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { StudioShell } from "./StudioShell";

afterEach(cleanup);

function renderShell() {
  return render(
    <StudioShell
      status={<span>statut-factice</span>}
      library={<span>bibliotheque-factice</span>}
      viewer={<span>visualiseur-factice</span>}
      panel={<span>panneau-factice</span>}
      queue={<span>file-factice</span>}
    />,
  );
}

const library = () => screen.getByRole("complementary", { name: "Bibliothèque" });
const panel = () => screen.getByRole("complementary", { name: "Nouvelle génération" });
const libraryToggle = () => screen.getByRole("button", { name: "Bibliothèque" });
const panelToggle = () => screen.getByRole("button", { name: "Nouvelle génération" });

describe("StudioShell", () => {
  it("place chaque contenu dans sa zone de la mise en page", () => {
    renderShell();
    expect(within(screen.getByRole("banner")).getByText("statut-factice")).toBeTruthy();
    expect(within(library()).getByText("bibliotheque-factice")).toBeTruthy();
    expect(within(screen.getByRole("main")).getByText("visualiseur-factice")).toBeTruthy();
    expect(within(panel()).getByText("panneau-factice")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "File d'attente" })).getByText("file-factice")).toBeTruthy();
  });

  it("nomme le produit et signale la page courante", () => {
    renderShell();
    expect(within(screen.getByRole("banner")).getByText("Hephaistos 3D")).toBeTruthy();
    const nav = screen.getByRole("navigation", { name: "Navigation principale" });
    expect(within(nav).getByRole("link", { name: "Studio" }).getAttribute("aria-current")).toBe("page");
  });

  it("relie chaque bouton au panneau qu'il commande, replié au départ", () => {
    renderShell();
    expect(libraryToggle().getAttribute("aria-expanded")).toBe("false");
    expect(panelToggle().getAttribute("aria-expanded")).toBe("false");
    expect(libraryToggle().getAttribute("aria-controls")).toBe(library().id);
    expect(panelToggle().getAttribute("aria-controls")).toBe(panel().id);
    expect(library().dataset.open).toBe("false");
    expect(panel().dataset.open).toBe("false");
  });

  it("ouvre puis referme un panneau", async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(libraryToggle());
    expect(library().dataset.open).toBe("true");
    expect(libraryToggle().getAttribute("aria-expanded")).toBe("true");
    await user.click(libraryToggle());
    expect(library().dataset.open).toBe("false");
    expect(libraryToggle().getAttribute("aria-expanded")).toBe("false");
  });

  it("n'ouvre qu'un panneau à la fois", async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(libraryToggle());
    await user.click(panelToggle());
    expect(library().dataset.open).toBe("false");
    expect(panel().dataset.open).toBe("true");
  });
});
