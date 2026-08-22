import { useEffect, useMemo, useState } from "react";
import { XIcon } from "lucide-react";
import { ModelValueCombobox } from "@/components/ModelValueCombobox";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { PolicyRegistryEntry } from "@/hooks/usePolicies";
import { coercePolicyParams } from "@/lib/policyParams";

export interface PolicyParamProperty {
  type?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  items?: { type?: string; enum?: string[]; "x-enum-source"?: string };
  uniqueItems?: boolean;
  "x-ui-widget"?: "textarea";
}

export type PolicyParamProperties = Record<string, PolicyParamProperty>;

interface PolicyParamsSchema {
  properties?: PolicyParamProperties;
  required?: string[];
}

export function policyParamProperties(
  entry: PolicyRegistryEntry | undefined,
  modelIds: string[],
): PolicyParamProperties {
  const schema = entry?.params_schema as PolicyParamsSchema | null | undefined;
  const properties = schema?.properties ?? {};
  if (!modelIds.length) return properties;

  return Object.fromEntries(
    Object.entries(properties).map(([key, property]) => [
      key,
      property.items?.["x-enum-source"] === "models" && !property.items.enum
        ? { ...property, items: { ...property.items, enum: modelIds } }
        : property,
    ]),
  );
}

export function policyParamsToFormValues(
  params: Record<string, unknown> | null | undefined,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params ?? {}).map(([key, value]) => {
      if (Array.isArray(value)) return [key, value.join(",")];
      if (value !== null && typeof value === "object") return [key, JSON.stringify(value)];
      return [key, value == null ? "" : String(value)];
    }),
  );
}

function PolicyInstanceFields({
  name,
  onNameChange,
  properties,
  factoryParams,
  onFactoryParamsChange,
}: {
  name: string;
  onNameChange: (name: string) => void;
  properties: PolicyParamProperties;
  factoryParams: Record<string, string>;
  onFactoryParamsChange: (params: Record<string, string>) => void;
}) {
  const setParam = (key: string, value: string) =>
    onFactoryParamsChange({ ...factoryParams, [key]: value });

  return (
    <>
      <div>
        <label htmlFor="policy-instance-name" className="text-sm font-medium text-foreground">
          name
        </label>
        <input
          id="policy-instance-name"
          type="text"
          value={name}
          onChange={(event) => onNameChange(event.target.value)}
          className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1.5 text-ui"
        />
      </div>
      {Object.entries(properties).length > 0 && (
        <div className="space-y-2">
          {Object.entries(properties).map(([key, property]) => {
            const currentArray = factoryParams[key]
              ? factoryParams[key].split(",").filter(Boolean)
              : Array.isArray(property.default)
                ? property.default
                : [];
            return (
              <div key={key}>
                <label
                  htmlFor={`policy-param-${key}`}
                  className="flex items-center gap-1 text-sm text-muted-foreground"
                >
                  <span className="font-medium text-foreground">{key}</span>
                  {property.type && (
                    <span>
                      (
                      {property.type === "array" && property.items?.enum
                        ? "multi-select"
                        : property.type === "array"
                          ? "comma-separated"
                          : property.type}
                      )
                    </span>
                  )}
                </label>
                {property.description && (
                  <p className="break-words text-sm text-muted-foreground">
                    {property.description}
                  </p>
                )}
                {property.type === "boolean" ? (
                  <select
                    id={`policy-param-${key}`}
                    value={
                      factoryParams[key] ??
                      (property.default !== undefined ? String(property.default) : "")
                    }
                    onChange={(event) => setParam(key, event.target.value)}
                    className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1.5 text-ui"
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : property.type === "string" && property.enum ? (
                  <select
                    id={`policy-param-${key}`}
                    value={
                      factoryParams[key] ??
                      (property.default !== undefined
                        ? String(property.default)
                        : (property.enum[0] ?? ""))
                    }
                    onChange={(event) => setParam(key, event.target.value)}
                    className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1.5 text-ui"
                  >
                    {property.enum.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                ) : property.type === "array" && property.items?.enum ? (
                  <div className="mt-0.5 space-y-1.5">
                    {currentArray.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {currentArray.map((value) => (
                          <span
                            key={value}
                            className="inline-flex items-center gap-0.5 rounded-md bg-muted px-1.5 py-0.5 text-sm"
                          >
                            {value}
                            <button
                              type="button"
                              aria-label={`Remove ${value}`}
                              onClick={() =>
                                setParam(
                                  key,
                                  currentArray.filter((item) => item !== value).join(","),
                                )
                              }
                              className="ml-0.5 text-muted-foreground hover:text-foreground"
                            >
                              <XIcon className="size-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    <ModelValueCombobox
                      options={property.items.enum}
                      selected={currentArray}
                      onToggle={(value) =>
                        setParam(
                          key,
                          (currentArray.includes(value)
                            ? currentArray.filter((item) => item !== value)
                            : [...currentArray, value]
                          ).join(","),
                        )
                      }
                    />
                  </div>
                ) : property.type === "string" && property["x-ui-widget"] === "textarea" ? (
                  <Textarea
                    id={`policy-param-${key}`}
                    value={factoryParams[key] ?? ""}
                    placeholder={
                      property.default !== undefined ? String(property.default) : undefined
                    }
                    onChange={(event) => setParam(key, event.target.value)}
                    rows={12}
                    className="mt-0.5 min-h-48 resize-y font-mono text-sm"
                  />
                ) : (
                  <input
                    id={`policy-param-${key}`}
                    type={
                      property.type === "integer" || property.type === "number" ? "number" : "text"
                    }
                    placeholder={
                      property.type === "array"
                        ? property.default !== undefined
                          ? (property.default as string[]).join(", ")
                          : "comma-separated values"
                        : property.default !== undefined
                          ? String(property.default)
                          : ""
                    }
                    value={factoryParams[key] ?? ""}
                    onChange={(event) => setParam(key, event.target.value)}
                    className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1.5 text-ui"
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

export { PolicyInstanceFields };

export function EditPolicyInstanceDialog({
  policy,
  registryEntry,
  modelIds,
  open,
  onOpenChange,
  onSave,
  isPending,
  error,
}: {
  policy: {
    name: string;
    handler: string;
    factory_params?: Record<string, unknown> | null;
  } | null;
  registryEntry: PolicyRegistryEntry | undefined;
  modelIds: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (payload: { name: string; factory_params: Record<string, unknown> | null }) => void;
  isPending: boolean;
  error: Error | null;
}) {
  const [name, setName] = useState("");
  const [factoryParams, setFactoryParams] = useState<Record<string, string>>({});
  const [paramError, setParamError] = useState<string | null>(null);
  const properties = useMemo(
    () => policyParamProperties(registryEntry, modelIds),
    [registryEntry, modelIds],
  );

  useEffect(() => {
    if (!open || !policy) return;
    const defaultParams = Object.fromEntries(
      Object.entries(properties)
        .filter(([, property]) => property.default !== undefined)
        .map(([key, property]) => [key, property.default]),
    );
    setName(policy.name);
    setFactoryParams({
      ...policyParamsToFormValues(defaultParams),
      ...policyParamsToFormValues(policy.factory_params),
    });
    setParamError(null);
  }, [open, policy, properties]);

  function handleSave() {
    if (!policy) return;
    if (registryEntry?.kind !== "factory") {
      onSave({ name, factory_params: policy.factory_params ?? null });
      return;
    }
    const keys = Object.keys(properties);
    const result = coercePolicyParams(keys, properties, factoryParams);
    if (!result.ok) {
      setParamError(result.error);
      return;
    }
    const unknownParams = Object.fromEntries(
      Object.entries(policy.factory_params ?? {}).filter(([key]) => !keys.includes(key)),
    );
    setParamError(null);
    onSave({ name, factory_params: { ...unknownParams, ...result.params } });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] w-[calc(100vw-2rem)] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Edit Policy</DialogTitle>
          <DialogDescription>
            Update this installed policy instance. Its handler and type cannot be changed.
          </DialogDescription>
        </DialogHeader>
        {policy && (
          <div className="min-w-0 space-y-3 pt-1">
            <div className="rounded border border-border bg-muted/50 px-2.5 py-2">
              <code className="break-all text-sm text-muted-foreground">{policy.handler}</code>
            </div>
            <PolicyInstanceFields
              name={name}
              onNameChange={setName}
              properties={registryEntry?.kind === "factory" ? properties : {}}
              factoryParams={factoryParams}
              onFactoryParamsChange={setFactoryParams}
            />
            {(paramError || error) && (
              <div
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui text-destructive"
              >
                {paramError ?? error?.message}
              </div>
            )}
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={handleSave} loading={isPending} disabled={!name.trim()}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
