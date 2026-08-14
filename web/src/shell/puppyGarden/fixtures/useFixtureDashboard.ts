import { useSyncExternalStore } from "react";
import { isPuppyGardenFixtureMode } from "./puppyGardenFixtureMode";
import { getFixtureDashboard, subscribeFixtureStore } from "./puppyGardenFixtureStore";

export function useFixtureDashboard(taskId: string) {
  const snapshot = useSyncExternalStore(
    subscribeFixtureStore,
    () => getFixtureDashboard(taskId),
    () => getFixtureDashboard(taskId),
  );
  return snapshot;
}

export function usePuppyGardenFixtureEnabled(): boolean {
  return isPuppyGardenFixtureMode();
}
