import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PuppyGardenPage } from "./PuppyGardenPage";

vi.mock("@/shell/PuppyGardenChatSidebar", () => ({
  PuppyGardenChatSidebar: () => <div data-testid="puppy-garden-chat-sidebar" />,
}));

afterEach(cleanup);

describe("PuppyGardenPage", () => {
  it("renders the board and chat sidebar side by side", () => {
    render(<PuppyGardenPage />);
    expect(screen.getByTestId("puppy-garden-page")).toBeInTheDocument();
    expect(screen.getByTestId("puppy-garden-board")).toBeInTheDocument();
    expect(screen.getByTestId("puppy-garden-chat-sidebar")).toBeInTheDocument();
  });
});
