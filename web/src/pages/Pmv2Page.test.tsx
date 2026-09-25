import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Pmv2Page } from "./Pmv2Page";

vi.mock("@/shell/Pmv2ChatSidebar", () => ({
  Pmv2ChatSidebar: () => <div data-testid="pmv2-chat-sidebar" />,
}));

vi.mock("@/shell/pmv2/Pmv2Board", () => ({
  Pmv2Board: () => <div data-testid="pmv2-board-content" />,
}));

afterEach(cleanup);

describe("Pmv2Page", () => {
  it("renders the board and chat sidebar side by side", () => {
    render(<Pmv2Page />);
    expect(screen.getByTestId("pmv2-page")).toBeInTheDocument();
    expect(screen.getByTestId("pmv2-board")).toBeInTheDocument();
    expect(screen.getByTestId("pmv2-chat-sidebar")).toBeInTheDocument();
  });
});
