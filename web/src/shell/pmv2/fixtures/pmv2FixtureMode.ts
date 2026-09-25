/** Dev-only pmv2 board fixture. Enable with `?fixture=1` on `/pmv2`. */

export const FIXTURE_IDLE_TASK_ID = "fixture-idle";
export const FIXTURE_ACTIVE_TASK_ID = "fixture-active";

export function isPmv2FixtureMode(): boolean {
  if (typeof window === "undefined") {
    return import.meta.env.VITE_PMV2_FIXTURE === "1";
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("fixture") === "1") return true;
  return import.meta.env.VITE_PMV2_FIXTURE === "1";
}
