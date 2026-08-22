import { describe, expect, it, vi } from "vitest";
import { installChunkRecovery } from "./chunkRecovery";

describe("installChunkRecovery", () => {
  it("reloads instead of leaving the app blank after a stale lazy chunk fails", () => {
    const target = new EventTarget();
    const reload = vi.fn();
    const cleanup = installChunkRecovery(target, reload);
    const event = new Event("vite:preloadError", { cancelable: true });

    target.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(reload).toHaveBeenCalledOnce();
    cleanup();
  });
});
