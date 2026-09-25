import { useSyncExternalStore } from "react";
import { isPmv2FixtureMode } from "./pmv2FixtureMode";
import { getFixtureDashboard, subscribeFixtureStore } from "./pmv2FixtureStore";

export function useFixtureDashboard(taskId: string) {
  const snapshot = useSyncExternalStore(
    subscribeFixtureStore,
    () => getFixtureDashboard(taskId),
    () => getFixtureDashboard(taskId),
  );
  return snapshot;
}

export function usePmv2FixtureEnabled(): boolean {
  return isPmv2FixtureMode();
}
