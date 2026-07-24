import { type RefObject, useLayoutEffect } from "react";

/**
 * Measure ``ta`` and set its height to fit its content, capped at
 * ``maxRows`` rows (after which it scrolls). When ``scrollHeight`` is 0 the
 * element isn't laid out yet (e.g. mid client-side route swap), so the
 * natural height is left untouched rather than collapsed to 0px, which
 * would clip the content/placeholder.
 */
function measureTextarea(ta: HTMLTextAreaElement, maxRows: number): void {
  const cs = getComputedStyle(ta);
  const lineHeight = parseFloat(cs.lineHeight);
  if (!Number.isFinite(lineHeight)) return;
  const paddingTop = parseFloat(cs.paddingTop);
  const paddingBottom = parseFloat(cs.paddingBottom);
  const maxHeight = lineHeight * maxRows + paddingTop + paddingBottom;

  // Collapse before measuring so scrollHeight reflects wrapped content at the
  // current width (not the previous inline height from a wider container).
  ta.style.minHeight = "0";
  ta.style.height = "0";
  const contentHeight = ta.scrollHeight;
  if (contentHeight === 0) {
    ta.style.height = "auto";
    ta.style.minHeight = "";
    return;
  }
  ta.style.height = `${Math.min(contentHeight, maxHeight)}px`;
  ta.style.minHeight = "";
}

/**
 * Auto-grow a textarea from a single row up to ``maxRows`` rows, then
 * let it scroll. Re-measures on every ``value`` change so the height
 * tracks the content.
 *
 * Shared by the in-session composer (ChatPage's ``Composer``) and the
 * home-page composer (``NewChatLandingScreen``) so their grow behavior
 * stays in lockstep instead of drifting between two copies.
 *
 * A ``ResizeObserver`` re-measures when the element's box changes,
 * covering the case where ``scrollHeight`` reads 0 on mount (e.g. mid
 * client-side route swap, before layout settles).
 */
export function useAutoGrowTextarea(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  maxRows = 10,
  /** Re-measure when a container mounts or resizes (e.g. side-panel swap). */
  layoutKey?: string | number | null,
) {
  // Re-measure on content / maxRows / layout change.
  useLayoutEffect(() => {
    const ta = ref.current;
    if (!ta) return;
    const measure = () => measureTextarea(ta, maxRows);
    measure();
    // One frame later so width-dependent wrapping has settled (narrow rails).
    const frame = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(frame);
  }, [ref, value, maxRows, layoutKey]);

  // Install one observer per element (not per keystroke — value isn't a
  // dep) so the box recovers once layout settles after a 0-height mount.
  // Setting height in measureTextarea converges (auto → fixed → same
  // fixed), so the observer settles rather than looping. Guarded for
  // environments (jsdom) without ResizeObserver.
  useLayoutEffect(() => {
    const ta = ref.current;
    if (!ta || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measureTextarea(ta, maxRows));
    ro.observe(ta);
    return () => ro.disconnect();
  }, [ref, maxRows]);
}
