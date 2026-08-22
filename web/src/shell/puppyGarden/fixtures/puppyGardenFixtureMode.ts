/** Dev-only PuppyGarden board fixture. Enable with `?fixture=1` on `/puppy-garden`. */

export const FIXTURE_PENDING_TASK_ID = "fixture-pending";
export const FIXTURE_ACTIVE_TASK_ID = "fixture-active";

export function isPuppyGardenFixtureMode(): boolean {
  if (typeof window === "undefined") {
    return import.meta.env.VITE_PUPPY_GARDEN_FIXTURE === "1";
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("fixture") === "1") return true;
  return import.meta.env.VITE_PUPPY_GARDEN_FIXTURE === "1";
}
