import { describe, expect, it } from "vitest";
import { applyPreset, formatValue, initialValues, paramsToSend, parseSchema, parseSeed, validate, type FormField } from "./engine";

const ui = (order: number, widget = "number", extra = {}) => ({ "x-ui": { widget, order, ...extra } });
const schema = (properties: Record<string, unknown>) => ({ type: "object", title: "X", additionalProperties: false, properties });

const fields = parseSchema(schema({
  tard: { type: "integer", title: "Tard", default: 5, minimum: 2, maximum: 8, ...ui(3, "slider") },
  couleur: { type: "string", title: "Couleur", enum: ["r", "v"], default: "r", ...ui(1, "radio", { labels: { r: "Rouge" } }) },
  pas: { type: "number", title: "Pas", default: 0.3, minimum: 0, multipleOf: 0.1, ...ui(2) },
  option: { anyOf: [{ type: "number", minimum: 1, maximum: 2 }, { type: "null" }], default: null, title: "Option", ...ui(4, "number", { advanced: true, unit: "cm" }) },
  taille: { type: "integer", title: "Taille", enum: [1024, 2048], default: 1024, ...ui(5, "select") },
  actif: { type: "boolean", title: "Actif", default: false, ...ui(6, "toggle") },
}));
const field = (name: string) => fields.find((f) => f.name === name) as FormField;
const valuesWith = (over: Record<string, string | boolean>) => ({ ...initialValues(fields), ...over });
const errorsOf = (over: Record<string, string | boolean>) => {
  const result = validate(fields, valuesWith(over));
  return result.ok ? {} : result.errors;
};

describe("parseSchema", () => {
  it("trie par ordre et normalise les champs", () => {
    expect(fields.map((f) => f.name)).toEqual(["couleur", "pas", "tard", "option", "taille", "actif"]);
    expect(field("option")).toMatchObject({ kind: "number", nullable: true, min: 1, max: 2, advanced: true, unit: "cm", default: null });
    expect(field("couleur").options).toEqual([{ value: "r", label: "Rouge" }, { value: "v", label: "v" }]);
    expect(field("taille").options?.[0]).toEqual({ value: 1024, label: "1 024" });
  });

  it.each([
    ["type inconnu", schema({ a: { type: "array", ...ui(1) } })],
    ["x-ui absent", schema({ a: { type: "integer" } })],
    ["widget inconnu", schema({ a: { type: "integer", ...ui(1, "color") } })],
    ["anyOf hors nullable", schema({ a: { anyOf: [{ type: "integer" }, { type: "string" }], ...ui(1) } })],
    ["pas un objet", null],
  ])("refuse %s", (_label, value) => {
    expect(() => parseSchema(value)).toThrow();
  });
});

describe("validate", () => {
  it("rend des valeurs typées", () => {
    expect(validate(fields, valuesWith({ tard: "7", pas: "0.7", option: "1.5", taille: "2048", actif: true }))).toEqual({
      ok: true,
      params: { couleur: "r", pas: 0.7, tard: 7, option: 1.5, taille: 2048, actif: true },
    });
  });

  it("accepte les bornes exactes", () => {
    expect(errorsOf({ tard: "2" })).toEqual({});
    expect(errorsOf({ tard: "8" })).toEqual({});
  });

  it("refuse hors bornes, non entier, non numérique et vide requis", () => {
    expect(errorsOf({ tard: "1" }).tard).toBe("Entrez une valeur d'au moins 2.");
    expect(errorsOf({ tard: "9" }).tard).toBe("Entrez une valeur d'au plus 8.");
    expect(errorsOf({ tard: "2.5" }).tard).toBe("Entrez un nombre entier.");
    expect(errorsOf({ pas: "abc" }).pas).toBe("Entrez un nombre.");
    expect(errorsOf({ tard: "" }).tard).toBe("Renseignez cette valeur.");
  });

  it("laisse vide un champ facultatif", () => {
    const result = validate(fields, valuesWith({ option: "" }));
    expect(result).toMatchObject({ ok: true, params: { option: null } });
  });

  it("applique multipleOf comme le backend, sans erreur d'arrondi", () => {
    expect(errorsOf({ pas: "3.3" })).toEqual({});
    expect(errorsOf({ pas: "0.7" })).toEqual({});
    expect(errorsOf({ pas: "3.33" }).pas).toBe("Entrez un multiple de 0,1.");
    expect(errorsOf({ pas: "1e-9" })).toEqual({});
    expect(errorsOf({ pas: "2e-9" }).pas).toBeTruthy();
    expect(errorsOf({ pas: "1000.0000001" }).pas).toBeTruthy();
  });

  it("refuse une valeur hors de la liste", () => {
    expect(errorsOf({ couleur: "b" }).couleur).toBe("Choisissez une valeur proposée.");
  });
});

describe("préréglages", () => {
  const presets = { a: { label: "A", values: { tard: 3 } }, b: { label: "B", values: { pas: 0.5 } } };

  it("part des défauts recouverts par le préréglage", () => {
    expect(initialValues(fields, presets.a.values)).toMatchObject({ tard: "3", pas: "0.3", option: "", actif: false });
  });

  it("change de préréglage sans toucher aux champs qu'aucun ne pilote", () => {
    const current = { ...initialValues(fields, presets.a.values), couleur: "v", tard: "7" };
    expect(applyPreset(fields, current, presets, "b")).toMatchObject({ couleur: "v", tard: "5", pas: "0.5" });
  });

  it("n'envoie que ce qui diffère du préréglage", () => {
    const baseline = { couleur: "r", pas: 0.3, tard: 3, option: null, taille: 1024, actif: false };
    expect(paramsToSend({ ...baseline, tard: 6, couleur: "v" }, baseline)).toEqual({ tard: 6, couleur: "v" });
    expect(paramsToSend(baseline, baseline)).toEqual({});
  });

  it("formate une valeur pour l'affichage", () => {
    expect(formatValue(field("couleur"), "r")).toBe("Rouge");
    expect(formatValue(field("taille"), 2048)).toBe("2 048");
    expect(formatValue(field("pas"), 0.5)).toBe("0,5");
  });
});

describe("parseSeed", () => {
  it.each([["", "random"], ["  ", "random"], ["0", "0"], ["2147483647", "2147483647"]])("accepte %j", (text, seed) => {
    expect(parseSeed(text)).toEqual({ ok: true, seed });
  });
  it.each(["2147483648", "-1", "1.5", "abc", "12345678901"])("refuse %j", (text) => {
    expect(parseSeed(text)).toMatchObject({ ok: false });
  });
});
