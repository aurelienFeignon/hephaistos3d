"use client";

import { useCallback, useId, useState, type FormEvent } from "react";
import { ApiError, createApiClient, type Model } from "@/lib/api/client";
import { useResource } from "@/lib/api/useResource";
import { applyPreset, baselineOf, formatValue, initialValues, paramsToSend, parseSchema, parseSeed, validate, type FormField, type FormValues } from "@/lib/forms/engine";
import { decodeInBrowser, type ImageDecoder } from "@/lib/image/inspect";
import { ImagePicker } from "./ImagePicker";
import { fieldInputClass, fieldLabelClass, ParamsForm } from "./ParamsForm";

const defaultApi = createApiClient();

export type GenerationApi = Pick<typeof defaultApi, "models" | "modelSchema" | "presets" | "createGeneration">;

type Presets = Awaited<ReturnType<GenerationApi["presets"]>>;
type Definition = { fields: FormField[]; presets: Presets };
type Feedback = { kind: "success" | "error"; text: string };

const API_MESSAGES: Record<string, string> = {
  UPLOAD_TOO_LARGE: "Cette image dépasse la limite du serveur.",
  REQUEST_TOO_LARGE: "Cette image dépasse la limite du serveur.",
  UNSUPPORTED_MEDIA_TYPE: "Le serveur n'accepte pas ce format d'image.",
  IMAGE_UNREADABLE: "Le serveur n'a pas pu lire cette image.",
  IMAGE_TOO_LARGE: "Cette image a trop de pixels pour le serveur.",
  PARAMS_INVALID: "Le serveur a refusé ces réglages. Vérifiez les valeurs et réessayez.",
  VALIDATION_ERROR: "Le serveur a refusé ces réglages. Vérifiez les valeurs et réessayez.",
  INSUFFICIENT_VRAM_BUDGET: "Ces réglages demandent plus de mémoire graphique que ce serveur n'en dispose. Choisissez un préréglage plus léger.",
  UPLOAD_CAPACITY_EXCEEDED: "Le serveur est occupé. Réessayez dans un instant.",
};

function messageFor(error: unknown) {
  return (error instanceof ApiError && API_MESSAGES[error.code]) || "La génération n'a pas pu être créée. Réessayez.";
}

function LoadFailure({ text, onRetry }: { text: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-start gap-2">
      <p role="alert" className="text-[13px] text-danger">
        {text}
      </p>
      <button type="button" className="min-h-11 rounded-md border border-line-strong px-3" onClick={onRetry}>
        Réessayer
      </button>
    </div>
  );
}

function ReadyForm({ api, model, definition, file, onCreated }: { api: GenerationApi; model: Model; definition: Definition; file: File | null; onCreated?: () => void }) {
  const { fields, presets } = definition;
  const presetKeys = Object.keys(presets);
  const [preset, setPreset] = useState<string | null>(model.default_preset && presets[model.default_preset] ? model.default_preset : (presetKeys[0] ?? null));
  const [values, setValues] = useState<FormValues>(() => initialValues(fields, preset ? presets[preset].values : undefined));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [seed, setSeed] = useState("");
  const [seedError, setSeedError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const id = useId();
  const presetValues = preset ? presets[preset].values : undefined;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || submitting) return;
    setFeedback(null);
    const checked = validate(fields, values);
    const parsedSeed = parseSeed(seed);
    setErrors(checked.ok ? {} : checked.errors);
    setSeedError(parsedSeed.ok ? null : parsedSeed.error);
    if (!checked.ok || !parsedSeed.ok) return;

    const body = new FormData();
    body.set("image", file);
    body.set("model_id", model.id);
    if (preset) body.set("preset", preset);
    body.set("params", JSON.stringify(paramsToSend(checked.params, baselineOf(fields, presetValues))));
    body.set("seed", parsedSeed.seed);
    setSubmitting(true);
    try {
      const created = await api.createGeneration(body);
      if (created.status === "FAILED") {
        setFeedback({ kind: "error", text: "L'image n'a pas pu être enregistrée. Importez-la de nouveau." });
      } else {
        setFeedback({ kind: "success", text: "Génération ajoutée à la file." });
        onCreated?.();
      }
    } catch (error) {
      setFeedback({ kind: "error", text: messageFor(error) });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form noValidate onSubmit={submit} className="flex flex-1 flex-col gap-3">
      {preset && (
        <fieldset className="flex flex-col gap-1 text-[13px] text-ink-muted">
          <legend>Préréglage</legend>
          <div className="flex flex-wrap gap-x-4 text-sm text-ink">
            {presetKeys.map((key) => (
              <label key={key} className="flex min-h-11 items-center gap-2">
                <input
                  type="radio"
                  name={`${id}-preset`}
                  className="accent-accent"
                  checked={preset === key}
                  onChange={() => {
                    setPreset(key);
                    setValues((current) => applyPreset(fields, current, presets, key));
                    setErrors({});
                  }}
                />
                {presets[key].label}
              </label>
            ))}
          </div>
          <dl data-testid="preset-values" className="grid grid-cols-[minmax(0,1fr)_auto] gap-y-1">
            {fields
              .filter((field) => presetValues && field.name in presetValues)
              .flatMap((field) => [
                <dt key={`${field.name}-t`}>{field.label}</dt>,
                <dd key={`${field.name}-d`} className="font-mono text-ink">
                  {formatValue(field, presetValues?.[field.name] as never)}
                </dd>,
              ])}
          </dl>
        </fieldset>
      )}

      <ParamsForm
        fields={fields}
        values={values}
        errors={errors}
        onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
        forceAdvancedOpen={seedError !== null}
        advancedExtra={
          <div className="flex flex-col gap-1">
            <div className={fieldLabelClass}>
              <label htmlFor={`${id}-seed`}>Graine</label>
              <input
                id={`${id}-seed`}
                type="text"
                inputMode="numeric"
                placeholder="Aléatoire"
                className={`${fieldInputClass} font-mono text-ink`}
                value={seed}
                aria-invalid={seedError !== null}
                aria-describedby={seedError ? `${id}-seed-error` : undefined}
                onChange={(event) => setSeed(event.target.value)}
              />
            </div>
            {seedError && (
              <p id={`${id}-seed-error`} className="text-[13px] text-danger">
                {seedError}
              </p>
            )}
          </div>
        }
      />

      <div className="sticky -bottom-4 -mb-4 mt-auto flex flex-col gap-2 bg-surface pt-2 pb-4">
        {feedback && (
          <p role={feedback.kind === "error" ? "alert" : "status"} className={`text-[13px] ${feedback.kind === "error" ? "text-danger" : "text-ink-muted"}`}>
            {feedback.text}
          </p>
        )}
        <button type="submit" disabled={!file || submitting} className="min-h-11 rounded-lg bg-accent px-3 font-medium text-white disabled:opacity-50">
          Générer
        </button>
      </div>
    </form>
  );
}

function ModelForm({ api, model, file, onCreated }: { api: GenerationApi; model: Model; file: File | null; onCreated?: () => void }) {
  const load = useCallback(
    async (signal: AbortSignal): Promise<Definition> => {
      const [schema, presets] = await Promise.all([api.modelSchema(model.id, signal), api.presets(model.id, signal)]);
      return { fields: parseSchema(schema), presets };
    },
    [api, model.id],
  );
  const { state, retry } = useResource(load);
  if (state.status === "error") return <LoadFailure text="Impossible d'afficher les réglages de ce modèle." onRetry={retry} />;
  if (state.status === "loading") return <p className="text-[13px] text-ink-muted">Chargement des réglages…</p>;
  return <ReadyForm api={api} model={model} definition={state.data} file={file} onCreated={onCreated} />;
}

export function GenerationPanel({ api = defaultApi, decode = decodeInBrowser, onCreated }: { api?: GenerationApi; decode?: ImageDecoder; onCreated?: () => void }) {
  const load = useCallback((signal: AbortSignal) => api.models(signal), [api]);
  const { state, retry } = useResource(load);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const selectId = useId();

  if (state.status === "error") return <LoadFailure text="Impossible de charger les modèles. Vérifiez que le service est démarré." onRetry={retry} />;
  if (state.status === "loading") return <p className="text-[13px] text-ink-muted">Chargement des modèles…</p>;
  const models = state.data;
  const model = models.find(({ id }) => id === chosenId) ?? models.find(({ available }) => available) ?? models[0];
  if (!model) return <p className="text-ink-muted">Aucun modèle n&apos;est installé.</p>;

  return (
    <div className="flex flex-1 flex-col gap-3">
      <div className={fieldLabelClass}>
        <label htmlFor={selectId}>Modèle</label>
        <select id={selectId} className={`${fieldInputClass} text-ink`} value={model.id} onChange={(event) => setChosenId(event.target.value)}>
          {models.map(({ id, name, available }) => (
            <option key={id} value={id} className={available ? undefined : "text-ink-faint"}>
              {available ? name : `${name} (indisponible)`}
            </option>
          ))}
        </select>
        {!model.available && <p className="text-xs">{`${model.unavailable_reason ?? "Modèle indisponible"}. Votre génération attendra dans la file.`}</p>}
      </div>
      <ImagePicker decode={decode} onChange={setFile} />
      <ModelForm key={model.id} api={api} model={model} file={file} onCreated={onCreated} />
    </div>
  );
}
