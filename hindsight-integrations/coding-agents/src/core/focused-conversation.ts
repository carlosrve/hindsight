import type { TransportTurn } from "./chat";

export interface DatedMessage {
  role: string;
  content: string;
  timestamp: string;
}
export interface FocusedConversationItem {
  message: DatedMessage;
  context_messages: DatedMessage[];
}
export const FOCUSED_CONVERSATION_CONTEXT =
  "Content has a target message and context_messages. Extract only what the target message asserts or proposes. " +
  "Context_messages are background used to resolve subjects and references, not independent memories to extract. " +
  "Resolve relative dates using the target message timestamp, which is the item's timestamp. " +
  "Do not promote a proposal into an execution or a user preference.";

function sourceTimestamp(value: string | undefined): string | undefined {
  // Date.parse accepts naive dates in the machine timezone; those are not source instants.
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return undefined;
  const time = Date.parse(value);
  return Number.isFinite(time) ? new Date(time).toISOString() : undefined;
}

/** Freeze each target's clock and nearby dialogue before queueing. Context stays in screened content. */
export function focusedConversationItems(
  turns: TransportTurn[],
  fallbackTimestamp: string,
  fromTurn = 0
): FocusedConversationItem[] {
  const fallback = sourceTimestamp(fallbackTimestamp) ?? new Date().toISOString();
  const messages = turns.map(
    (turn): DatedMessage => ({
      role: turn.role,
      content: turn.content,
      timestamp: sourceTimestamp(turn.timestamp) ?? fallback,
    })
  );
  const items: FocusedConversationItem[] = [];
  let user: DatedMessage | undefined;
  let assistant: DatedMessage | undefined;
  for (const [index, message] of messages.entries()) {
    const context = message.role === "user" ? assistant : user;
    if (index >= fromTurn)
      items.push({ message, context_messages: context ? [{ ...context }] : [] });
    if (message.role === "user") user = message;
    if (message.role === "assistant") assistant = message;
  }
  return items;
}
