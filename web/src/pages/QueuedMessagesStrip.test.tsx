// Tests for QueuedMessagesStrip — the presentational strip above the composer
// listing messages queued while the agent is busy. It's a pure prop-driven
// component (no store access), so we exercise it with plain props.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { QueuedMessage } from "@/store/chatStore";
import { QueuedMessagesStrip } from "./QueuedMessagesStrip";

const msg = (queueId: string, text: string): QueuedMessage => ({
  queueId,
  text,
  conversationId: "conv_abc",
});
const draft = (queueId: string, text: string): QueuedMessage => ({
  ...msg(queueId, text),
  kind: "draft",
});

afterEach(cleanup);

describe("QueuedMessagesStrip", () => {
  it("renders nothing when the queue is empty", () => {
    const { container } = render(
      <QueuedMessagesStrip messages={[]} onDelete={vi.fn()} onEdit={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders one row per queued message, in order", () => {
    render(
      <QueuedMessagesStrip
        messages={[msg("q_1", "first"), msg("q_2", "second")]}
        onDelete={vi.fn()}
        onEdit={vi.fn()}
      />,
    );
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText("second")).toBeInTheDocument();
  });

  it("calls onDelete with the row's queueId when its remove button is clicked", () => {
    const onDelete = vi.fn();
    render(
      <QueuedMessagesStrip
        messages={[msg("q_1", "first"), msg("q_2", "second")]}
        onDelete={onDelete}
        onEdit={vi.fn()}
      />,
    );
    const buttons = screen.getAllByRole("button", { name: "Remove queued message" });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[1]!);
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onDelete).toHaveBeenCalledWith("q_2");
  });

  it("calls onEdit with the row's queueId when its edit button is clicked", () => {
    const onEdit = vi.fn();
    render(
      <QueuedMessagesStrip
        messages={[msg("q_1", "first"), msg("q_2", "second")]}
        onDelete={vi.fn()}
        onEdit={onEdit}
      />,
    );
    const buttons = screen.getAllByRole("button", { name: "Edit queued message" });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0]!);
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledWith("q_1");
  });

  it("shows no steer or send button when onSteer and onSendNow are omitted", () => {
    render(
      <QueuedMessagesStrip messages={[msg("q_1", "first")]} onDelete={vi.fn()} onEdit={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Steer queued message into running turn" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Send now, interrupting the current turn" })).toBeNull();
  });

  it("calls onSteer with the row's queueId when its steer button is clicked", () => {
    const onSteer = vi.fn();
    render(
      <TooltipProvider>
        <QueuedMessagesStrip
          messages={[msg("q_1", "first"), msg("q_2", "second")]}
          onDelete={vi.fn()}
          onEdit={vi.fn()}
          onSteer={onSteer}
        />
      </TooltipProvider>,
    );
    const buttons = screen.getAllByRole("button", { name: "Steer queued message into running turn" });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[1]!);
    expect(onSteer).toHaveBeenCalledTimes(1);
    expect(onSteer).toHaveBeenCalledWith("q_2");
  });

  it("calls onSendNow with the row's queueId when its send button is clicked", () => {
    const onSendNow = vi.fn();
    render(
      <QueuedMessagesStrip
        messages={[msg("q_1", "first"), msg("q_2", "second")]}
        onDelete={vi.fn()}
        onEdit={vi.fn()}
        onSendNow={onSendNow}
      />,
    );
    const buttons = screen.getAllByRole("button", { name: "Send now, interrupting the current turn" });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0]!);
    expect(onSendNow).toHaveBeenCalledTimes(1);
    expect(onSendNow).toHaveBeenCalledWith("q_1");
  });

  it("shows a single Steer action for a draft during an active turn", () => {
    const onSteer = vi.fn();
    render(
      <QueuedMessagesStrip
        messages={[draft("q_1", "later")]}
        onDelete={vi.fn()}
        onEdit={vi.fn()}
        onSteer={onSteer}
        onSendNow={vi.fn()}
        turnActive
      />,
    );

    expect(screen.getByText("Draft")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Steer draft into running turn" }));
    expect(onSteer).toHaveBeenCalledWith("q_1");
    expect(
      screen.queryByRole("button", { name: "Send now, interrupting the current turn" }),
    ).toBeNull();
  });

  it("changes the draft action to Send when no turn is active", () => {
    const onSteer = vi.fn();
    render(
      <QueuedMessagesStrip
        messages={[draft("q_1", "later")]}
        onDelete={vi.fn()}
        onEdit={vi.fn()}
        onSteer={onSteer}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Send draft" }));
    expect(onSteer).toHaveBeenCalledWith("q_1");
  });

  it("shows a drag handle per row only when onReorder is provided", () => {
    const { rerender } = render(
      <QueuedMessagesStrip messages={[msg("q_1", "first")]} onDelete={vi.fn()} onEdit={vi.fn()} />,
    );
    // No reorder handler → no grip (the row shows the clock icon instead).
    expect(screen.queryByRole("button", { name: "Reorder queued message" })).toBeNull();

    rerender(
      <QueuedMessagesStrip
        messages={[msg("q_1", "first"), msg("q_2", "second")]}
        onDelete={vi.fn()}
        onEdit={vi.fn()}
        onReorder={vi.fn()}
      />,
    );
    expect(screen.getAllByRole("button", { name: "Reorder queued message" })).toHaveLength(2);
  });
});
