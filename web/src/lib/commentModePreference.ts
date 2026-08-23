export type CommentMode = "batch" | "threaded";

export const COMMENT_MODE_STORAGE_KEY = "omnigent:agent-comment-mode:v1";

export function readCommentMode(): CommentMode {
  if (typeof window === "undefined") return "batch";
  return window.localStorage.getItem(COMMENT_MODE_STORAGE_KEY) === "threaded"
    ? "threaded"
    : "batch";
}

export function writeCommentMode(mode: CommentMode): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(COMMENT_MODE_STORAGE_KEY, mode);
}
