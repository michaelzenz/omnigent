export const ONIH_SETTINGS_KEY = "omniharness";
export const ONIH_OPENAI_AGENTS_TARGET = "onih-openai-agents";
export const ONIH_PI_TARGET = "onih-pi";
export const ONIH_TARGET_NAMES = new Set([ONIH_OPENAI_AGENTS_TARGET, ONIH_PI_TARGET]);
export const LEGACY_OMNIHARNESS_TARGET = "omniharness";
export const OPENAI_AGENTS_ADAPTER = "openai-agents";

// Retained for settings/local-storage call sites while they migrate to the
// explicit settings-key name. It is not an execution-target identity.
export const OMNIHARNESS_AGENT_NAME = ONIH_SETTINGS_KEY;

export function onihRootTargetName(name: string | null | undefined): string | null {
  if (!name) return null;
  return name.replace(/ \((?:fork|switch) [^)]+\).*$/, "");
}

export function isOnihTargetName(name: string | null | undefined): boolean {
  const root = onihRootTargetName(name);
  return root !== null && ONIH_TARGET_NAMES.has(root);
}

/** True for the onih-pi execution target (fork/switch suffixes stripped). */
export function isOnihPiTargetName(name: string | null | undefined): boolean {
  return onihRootTargetName(name) === ONIH_PI_TARGET;
}

export interface OmniHarnessModelOption {
  id: string;
  displayName: string;
}

export const EMPTY_OMNIHARNESS_MODEL_OPTIONS: readonly OmniHarnessModelOption[] = [];
