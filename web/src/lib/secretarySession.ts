import type { SecretaryProfile } from "@/lib/agentTasksApi";

export function secretaryConversationId(
  profile: SecretaryProfile | null | undefined,
): string | null {
  return profile?.conversation_id ?? null;
}

export function withoutSecretaryConversation<T extends { id: string }>(
  conversations: T[],
  secretaryId: string | null,
): T[] {
  if (!secretaryId) return conversations;
  return conversations.filter((conversation) => conversation.id !== secretaryId);
}
