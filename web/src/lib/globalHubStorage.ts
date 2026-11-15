import { getOmnigentServerIdentity } from "./host";
import { getCurrentUserId } from "./identity";

/**
 * The local storage key for a value that the Global Hub keeps in the browser.
 * The key names the server and the signed-in user, so accounts that share a
 * browser keep their own values.
 *
 * @param name - The value's name, e.g. ``"chat-width-px"``.
 * @returns The key, or ``null`` until the server and the user are known.
 */
export function globalHubStorageKey(name: string): string | null {
  const server = getOmnigentServerIdentity();
  const user = getCurrentUserId();
  if (server === null || user === null) return null;
  return `omnigent.globalhub.${name}:${JSON.stringify([server, user])}`;
}
