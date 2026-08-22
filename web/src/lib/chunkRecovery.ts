/**
 * Reload the SPA when a deploy replaces a lazy-loaded chunk while this
 * renderer is still running the previous asset manifest.
 */
export function installChunkRecovery(
  target: EventTarget = window,
  reload: () => void = () => window.location.reload(),
): () => void {
  const onPreloadError = (event: Event) => {
    // Vite throws the failed dynamic-import error after this event unless it
    // is cancelled. Reloading fetches the new index and matching chunk names.
    event.preventDefault();
    reload();
  };
  target.addEventListener("vite:preloadError", onPreloadError);
  return () => target.removeEventListener("vite:preloadError", onPreloadError);
}
