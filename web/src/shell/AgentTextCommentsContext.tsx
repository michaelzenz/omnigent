import { createContext, useContext, type ReactNode } from "react";
import type { AgentTextCommentAnchor } from "@/hooks/useAgentTextComments";

export interface AgentTextCommentsUI {
  canEdit: boolean;
  pendingAnchor: AgentTextCommentAnchor | null;
  activeCommentId: string | null;
  openDraft: (anchor: AgentTextCommentAnchor) => void;
  cancelDraft: () => void;
  activateComment: (commentId: string | null) => void;
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
