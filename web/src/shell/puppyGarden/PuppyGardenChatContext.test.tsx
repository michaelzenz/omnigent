import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  PuppyGardenChatProvider,
  usePuppyGardenChat,
} from "./PuppyGardenChatContext";

function Probe() {
  const chat = usePuppyGardenChat();
  return (
    <div>
      <span data-testid="target-kind">{chat.target.kind}</span>
      <span data-testid="target-role">{chat.target.kind === "role" ? chat.target.role : ""}</span>
      <button type="button" onClick={() => chat.setRole("broker")}>
        broker
      </button>
      <button
        type="button"
        onClick={() => chat.openManager("t1", "mgr-1", "My task")}
      >
        manager
      </button>
      <button
        type="button"
        onClick={() => chat.openWorker("t1", "w1", "worker-1", "CI Fixer")}
      >
        worker
      </button>
      <button type="button" onClick={() => chat.dismissToRole()}>
        dismiss
      </button>
    </div>
  );
}

afterEach(cleanup);

describe("PuppyGardenChatContext", () => {
  it("defaults to the secretary role chat", () => {
    render(
      <PuppyGardenChatProvider>
        <Probe />
      </PuppyGardenChatProvider>,
    );
    expect(screen.getByTestId("target-kind")).toHaveTextContent("role");
    expect(screen.getByTestId("target-role")).toHaveTextContent("secretary");
  });

  it("switches role, opens manager/worker targets, and dismisses back to role", () => {
    render(
      <PuppyGardenChatProvider>
        <Probe />
      </PuppyGardenChatProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "broker" }));
    expect(screen.getByTestId("target-role")).toHaveTextContent("broker");

    fireEvent.click(screen.getByRole("button", { name: "manager" }));
    expect(screen.getByTestId("target-kind")).toHaveTextContent("manager");

    fireEvent.click(screen.getByRole("button", { name: "dismiss" }));
    expect(screen.getByTestId("target-kind")).toHaveTextContent("role");
    expect(screen.getByTestId("target-role")).toHaveTextContent("broker");

    fireEvent.click(screen.getByRole("button", { name: "worker" }));
    expect(screen.getByTestId("target-kind")).toHaveTextContent("worker");

    fireEvent.click(screen.getByRole("button", { name: "dismiss" }));
    expect(screen.getByTestId("target-kind")).toHaveTextContent("role");
  });
});
