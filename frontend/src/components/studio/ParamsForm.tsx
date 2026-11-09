"use client";

import { useId, useState, type ReactNode } from "react";
import type { FormField, FormValues } from "@/lib/forms/engine";

export const fieldInputClass =
  "min-h-10 w-full rounded-md border border-line-strong bg-surface px-2 aria-invalid:border-danger";
export const fieldLabelClass = "flex flex-col gap-1 text-[13px] text-ink-muted";

type ParamsFormProps = {
  fields: FormField[];
  values: FormValues;
  errors: Record<string, string>;
  onChange: (name: string, value: string | boolean) => void;
  advancedExtra?: ReactNode;
  forceAdvancedOpen?: boolean;
};

type ControlProps = { field: FormField; value: string | boolean; onChange: (value: string | boolean) => void; label: string; describedBy?: string; invalid: boolean; id: string };

const choicesOf = (field: FormField) => [
  ...(field.nullable ? [{ value: "", label: "Aucun" }] : []),
  ...(field.options ?? []).map(({ value, label }) => ({ value: String(value), label })),
];

function RadioControl({ field, value, onChange, label, describedBy, id }: ControlProps) {
  return (
    <fieldset aria-describedby={describedBy} className="flex flex-col gap-1 text-[13px] text-ink-muted">
      <legend>{label}</legend>
      <div className="flex flex-wrap gap-x-4 text-sm text-ink">
        {choicesOf(field).map((choice) => (
          <label key={choice.value} className="flex min-h-11 items-center gap-2">
            <input type="radio" name={id} className="accent-accent" checked={value === choice.value} onChange={() => onChange(choice.value)} />
            {choice.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function SelectControl({ field, value, onChange, label, describedBy, invalid, id }: ControlProps) {
  return (
    <div className={fieldLabelClass}>
      <label htmlFor={id}>{label}</label>
      <select id={id} className={`${fieldInputClass} text-ink`} value={String(value)} aria-describedby={describedBy} aria-invalid={invalid} onChange={(event) => onChange(event.target.value)}>
        {choicesOf(field).map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function ToggleControl({ value, onChange, label, describedBy }: ControlProps) {
  return (
    <label className="flex min-h-11 items-center gap-2 text-sm">
      <input type="checkbox" className="accent-accent" aria-describedby={describedBy} checked={value === true} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}

function NumberControl({ field, value, onChange, label, describedBy, invalid, id }: ControlProps) {
  const isText = field.kind === "string";
  const step = field.step ?? (field.kind === "integer" ? 1 : "any");
  const showSlider = field.widget === "slider" && field.min !== undefined && field.max !== undefined;
  return (
    <div className={fieldLabelClass}>
      <label htmlFor={id}>{label}</label>
      <div className="flex items-center gap-3">
        {showSlider && (
          <input type="range" aria-label={label} className="min-w-0 flex-1 accent-accent" min={field.min} max={field.max} step={step} value={String(value)} onChange={(event) => onChange(event.target.value)} />
        )}
        <input
          id={id}
          type={isText ? "text" : "number"}
          className={`${fieldInputClass} font-mono text-ink ${showSlider ? "w-24! shrink-0" : ""}`}
          min={field.min}
          max={field.max}
          step={isText ? undefined : step}
          value={String(value)}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    </div>
  );
}

function Control(props: ControlProps) {
  const { field } = props;
  if (field.kind === "boolean") return <ToggleControl {...props} />;
  if (field.options && field.widget === "radio") return <RadioControl {...props} />;
  if (field.options) return <SelectControl {...props} />;
  return <NumberControl {...props} />;
}

function Field({ field, value, error, onChange }: { field: FormField; value: string | boolean; error?: string; onChange: (value: string | boolean) => void }) {
  const id = useId();
  const descriptionId = field.description ? `${id}-description` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;
  const label = field.unit ? `${field.label} (${field.unit})` : field.label;
  return (
    <div className="flex flex-col gap-1">
      <Control field={field} value={value} onChange={onChange} label={label} describedBy={describedBy} invalid={Boolean(error)} id={id} />
      {field.description && (
        <p id={descriptionId} className="text-xs text-ink-faint">
          {field.description}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-[13px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function ParamsForm({ fields, values, errors, onChange, advancedExtra, forceAdvancedOpen = false }: ParamsFormProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const render = (field: FormField) => (
    <Field key={field.name} field={field} value={values[field.name]} error={errors[field.name]} onChange={(value) => onChange(field.name, value)} />
  );
  const advanced = fields.filter((field) => field.advanced);
  const mustOpen = forceAdvancedOpen || advanced.some((field) => errors[field.name]);
  return (
    <>
      {fields.filter((field) => !field.advanced).map(render)}
      {(advanced.length > 0 || advancedExtra) && (
        <details open={advancedOpen || mustOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
          <summary className="flex min-h-11 cursor-pointer items-center font-medium">Avancé</summary>
          <div className="flex flex-col gap-3">
            {advanced.map(render)}
            {advancedExtra}
          </div>
        </details>
      )}
    </>
  );
}
