import { z } from "zod";

// Sous-ensemble JSON Schema garanti par `adapters/schema.py` côté backend.
const WIDGETS = ["slider", "number", "select", "radio", "toggle"] as const;
const kind = z.enum(["integer", "number", "boolean", "string"]);
const primitive = z.union([z.string(), z.number(), z.boolean()]);
const constraints = {
  enum: z.array(primitive).optional(),
  minimum: z.number().optional(),
  maximum: z.number().optional(),
  multipleOf: z.number().positive().optional(),
};
const propertySchema = z
  .object({
    type: kind.optional(),
    ...constraints,
    title: z.string().optional(),
    description: z.string().optional(),
    default: z.unknown(),
    anyOf: z
      .array(z.union([z.strictObject({ type: z.literal("null") }), z.strictObject({ type: kind, ...constraints })]))
      .length(2)
      .optional(),
    "x-ui": z.strictObject({
      widget: z.enum(WIDGETS),
      order: z.number().int(),
      group: z.string().optional(),
      advanced: z.boolean().optional(),
      unit: z.string().optional(),
      labels: z.record(z.string(), z.string()).optional(),
    }),
  })
  .strict();
const documentSchema = z.object({ properties: z.record(z.string(), propertySchema) });

type Primitive = z.infer<typeof primitive>;
export type ParamValue = Primitive | null;
export type FormValues = Record<string, string | boolean>;
export type Params = Record<string, ParamValue>;
export type FieldOption = { value: Primitive; label: string };

export type FormField = {
  name: string;
  label: string;
  description?: string;
  kind: z.infer<typeof kind>;
  nullable: boolean;
  default: ParamValue;
  widget: (typeof WIDGETS)[number];
  order: number;
  advanced: boolean;
  unit?: string;
  options?: FieldOption[];
  min?: number;
  max?: number;
  step?: number;
};

export type Validation = { ok: true; params: Params } | { ok: false; errors: Record<string, string> };
export type Seed = { ok: true; seed: string } | { ok: false; error: string };

const SEED_MAX = 2 ** 31 - 1;
const number = new Intl.NumberFormat("fr-FR");

export function parseSchema(schema: unknown): FormField[] {
  const { properties } = documentSchema.parse(schema);
  return Object.entries(properties)
    .map(([name, property]) => toField(name, property))
    .sort((a, b) => a.order - b.order);
}

function toField(name: string, property: z.infer<typeof propertySchema>): FormField {
  const branch = property.anyOf ? property.anyOf.find((option) => option.type !== "null") : property;
  const nullableCount = property.anyOf?.filter((option) => option.type === "null").length;
  if (!branch?.type || (property.anyOf && nullableCount !== 1)) {
    throw new Error(`Champ hors du sous-ensemble : ${name}`);
  }
  const { enum: choices, minimum, maximum, multipleOf } = branch;
  const ui = property["x-ui"];
  return {
    name,
    label: property.title ?? name,
    description: property.description,
    kind: branch.type,
    nullable: property.anyOf !== undefined,
    default: (property.default ?? null) as ParamValue,
    widget: ui.widget,
    order: ui.order,
    advanced: ui.advanced ?? false,
    unit: ui.unit,
    options: choices?.map((value) => ({ value, label: ui.labels?.[String(value)] ?? displayPrimitive(value) })),
    min: minimum,
    max: maximum,
    step: multipleOf,
  };
}

function displayPrimitive(value: Primitive) {
  if (typeof value === "number") return number.format(value);
  if (typeof value === "boolean") return value ? "Oui" : "Non";
  return value;
}

export function formatValue(field: FormField, value: ParamValue) {
  if (value === null) return "Aucun";
  return field.options?.find((option) => option.value === value)?.label ?? displayPrimitive(value);
}

export function baselineOf(fields: FormField[], presetValues: Record<string, unknown> = {}): Params {
  return Object.fromEntries(
    fields.map((field) => [field.name, field.name in presetValues ? (presetValues[field.name] as ParamValue) : field.default]),
  );
}

function toInput(value: ParamValue) {
  if (typeof value === "boolean") return value;
  return value === null ? "" : String(value);
}

export function initialValues(fields: FormField[], presetValues?: Record<string, unknown>): FormValues {
  const baseline = baselineOf(fields, presetValues);
  return Object.fromEntries(fields.map((field) => [field.name, toInput(baseline[field.name])]));
}

// Un champ qu'aucun préréglage ne pilote décrit l'entrée, pas la qualité : il garde sa saisie.
export function applyPreset(
  fields: FormField[],
  current: FormValues,
  presets: Record<string, { values: Record<string, unknown> }>,
  key: string,
): FormValues {
  const next = initialValues(fields, presets[key].values);
  const piloted = new Set(Object.values(presets).flatMap((preset) => Object.keys(preset.values)));
  return Object.fromEntries(fields.map((field) => [field.name, piloted.has(field.name) ? next[field.name] : current[field.name]]));
}

// Tolérance absolue mesurée sur pydantic-core 2.50 : 3,3 n'est multiple de 0,1 qu'à l'arrondi flottant près.
const MULTIPLE_TOLERANCE = 1e-9;

function isMultiple(value: number, step: number) {
  const remainder = Math.abs(value % step);
  return remainder <= MULTIPLE_TOLERANCE || Math.abs(remainder - step) <= MULTIPLE_TOLERANCE;
}

function readField(field: FormField, raw: string | boolean): { value: ParamValue } | { error: string } {
  if (typeof raw === "boolean") return { value: raw };
  const text = raw.trim();
  if (text === "") return field.nullable ? { value: null } : { error: "Renseignez cette valeur." };
  if (field.options) {
    const option = field.options.find(({ value }) => String(value) === text);
    return option ? { value: option.value } : { error: "Choisissez une valeur proposée." };
  }
  if (field.kind === "string") return { value: text };
  const value = Number(text);
  if (!Number.isFinite(value)) return { error: "Entrez un nombre." };
  if (field.kind === "integer" && !Number.isInteger(value)) return { error: "Entrez un nombre entier." };
  if (field.min !== undefined && value < field.min) return { error: `Entrez une valeur d'au moins ${number.format(field.min)}.` };
  if (field.max !== undefined && value > field.max) return { error: `Entrez une valeur d'au plus ${number.format(field.max)}.` };
  if (field.step !== undefined && !isMultiple(value, field.step)) return { error: `Entrez un multiple de ${number.format(field.step)}.` };
  return { value };
}

export function validate(fields: FormField[], values: FormValues): Validation {
  const params: Params = {};
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const result = readField(field, values[field.name]);
    if ("error" in result) errors[field.name] = result.error;
    else params[field.name] = result.value;
  }
  return Object.keys(errors).length ? { ok: false, errors } : { ok: true, params };
}

export function paramsToSend(params: Params, baseline: Params): Params {
  return Object.fromEntries(Object.entries(params).filter(([name, value]) => value !== baseline[name]));
}

export function parseSeed(text: string): Seed {
  const seed = text.trim();
  if (seed === "") return { ok: true, seed: "random" };
  if (/^[0-9]{1,10}$/.test(seed) && Number(seed) <= SEED_MAX) return { ok: true, seed };
  return { ok: false, error: `Entrez un entier entre 0 et ${number.format(SEED_MAX)}, ou laissez vide pour un tirage au hasard.` };
}
