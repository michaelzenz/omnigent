import { createStoredChoicePreference } from "./storedChoicePreference";

export type ChatTopButtonMode = "jump-to-top" | "jump-to-previous-message";

export const CHAT_TOP_BUTTON_STORAGE_KEY = "omnigent:chat-top-button";

const preference = createStoredChoicePreference<ChatTopButtonMode>(
  CHAT_TOP_BUTTON_STORAGE_KEY,
  ["jump-to-top", "jump-to-previous-message"],
  "jump-to-top",
);

export const writeChatTopButtonMode = preference.write;

export const useChatTopButtonMode = preference.use;
