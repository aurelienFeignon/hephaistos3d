// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { initialValues, parseSchema, type FormValues } from "@/lib/forms/engine";
import { ParamsForm } from "./ParamsForm";

afterEach(cleanup);

// Schéma volontairement sans rapport avec un modèle réel (A3.3).
const fields = parseSchema({
  type: "object",
  additionalProperties: false,
  properties: {
    zebres: { type: "integer", title: "Zèbres", default: 4, minimum: 2, maximum: 8, "x-ui": { widget: "slider", order: 2 } },
    robe: { type: "string", title: "Robe", enum: ["r", "v"], default: "r", description: "La couleur.", "x-ui": { widget: "radio", order: 1, labels: { r: "Rayée", v: "Unie" } } },
    actif: { type: "boolean", title: "Actif", default: true, "x-ui": { widget: "toggle", order: 3 } },
    longueur: { anyOf: [{ type: "number", minimum: 1 }, { type: "null" }], default: null, title: "Longueur", "x-ui": { widget: "number", order: 4, unit: "cm", advanced: true } },
    enclos: { anyOf: [{ type: "string", enum: ["n", "s"] }, { type: "null" }], default: null, title: "Enclos", "x-ui": { widget: "select", order: 5, advanced: true } },
  },
});

function Harness({ errors = {} }: { errors?: Record<string, string> }) {
  const [values, setValues] = useState<FormValues>(() => initialValues(fields));
  return (
    <>
      <ParamsForm fields={fields} values={values} errors={errors} onChange={(name, value) => setValues((v) => ({ ...v, [name]: value }))} />
      <output>{JSON.stringify(values)}</output>
    </>
  );
}
const state = () => JSON.parse(screen.getByRole("status").textContent ?? "{}");

describe("ParamsForm", () => {
  it("rend chaque type de champ depuis un schéma arbitraire", () => {
    render(<Harness />);
    expect(screen.getByRole("radio", { name: "Rayée" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Unie" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "Robe" })).toBeTruthy();
    expect(screen.getByRole("slider", { name: "Zèbres" })).toBeTruthy();
    expect(screen.getByRole("spinbutton", { name: "Zèbres" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Actif" })).toBeTruthy();
    expect(screen.getByRole("spinbutton", { name: "Longueur (cm)" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Enclos" })).toBeTruthy();
  });

  it("place les champs avancés dans la section Avancé", () => {
    render(<Harness />);
    const details = screen.getByText("Avancé").closest("details") as HTMLDetailsElement;
    expect(details.contains(screen.getByRole("spinbutton", { name: "Longueur (cm)" }))).toBe(true);
    expect(details.contains(screen.getByRole("checkbox", { name: "Actif" }))).toBe(false);
  });

  it("affiche la description du champ", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "Robe" }).getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getByText("La couleur.")).toBeTruthy();
  });

  it("remonte chaque saisie", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "Unie" }));
    await user.click(screen.getByRole("checkbox", { name: "Actif" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Enclos" }), "s");
    const zebres = screen.getByRole("spinbutton", { name: "Zèbres" });
    await user.clear(zebres);
    await user.type(zebres, "6");
    expect(state()).toMatchObject({ robe: "v", actif: false, enclos: "s", zebres: "6" });
    expect((screen.getByRole("slider", { name: "Zèbres" }) as HTMLInputElement).value).toBe("6");
  });

  it("propose de ne rien choisir pour une liste facultative", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const enclos = screen.getByRole("combobox", { name: "Enclos" });
    await user.selectOptions(enclos, "n");
    await user.selectOptions(enclos, "Aucun");
    expect(state().enclos).toBe("");
  });

  it("relie l'erreur au champ et ouvre Avancé si elle s'y trouve", () => {
    render(<Harness errors={{ longueur: "Trop court." }} />);
    const input = screen.getByRole("spinbutton", { name: "Longueur (cm)" });
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(document.getElementById(input.getAttribute("aria-describedby") ?? "")?.textContent).toBe("Trop court.");
    expect((screen.getByText("Avancé").closest("details") as HTMLDetailsElement).open).toBe(true);
  });
});
