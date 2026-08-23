import { createContext, useContext, type ReactNode } from "react";
import type { AgentTextCommentAnchor } from "@/hooks/useAgentTextComments";
import type { CommentMode } from "@/lib/commentModePreference";

export interface AgentTextCommentSendState {
  isSending: boolean;
  sentBatchIds: string[] | null;
}

export interface AgentTextCommentsUI {
  canEdit: boolean;
  mode: CommentMode;
  preferredMode: CommentMode;
  threadedModeError: string | null;
  threadedModeLoading: boolean;
  setMode: (mode: CommentMode) => void;
  pendingAnchor: AgentTextCommentAnchor | null;
  activeCommentId: string | null;
  sendState: AgentTextCommentSendState;
  openDraft: (anchor: AgentTextCommentAnchor) => void;
  cancelDraft: () => void;
  activateComment: (commentId: string | null) => void;
  setSendState: (state: AgentTextCommentSendState) => void;
}

const Context = createContext<AgentTextCommentsUI | null>(null);

export function AgentTextCommentsProvider({
  value,
  children,
}: {
  value: AgentTextCommentsUI;
  children: ReactNode;
}) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAgentTextCommentsUI(): AgentTextCommentsUI | null {
  return useContext(Context);
}
