import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SearchPage } from "./SearchPage";

const useConversations = vi.fn();
vi.mock("@/hooks/useConversations", () => ({
  useConversations: (...args: unknown[]) => useConversations(...args),
}));

afterEach(cleanup);

describe("SearchPage", () => {
  it("shows active and archived chats with six-line excerpts", () => {
    useConversations.mockReturnValue({
      data: {
        pages: [
          {
            data: [
              {
                id: "active",
                title: "Deploy notes",
                labels: {},
                agent_name: "research",
                archived: false,
                updated_at: 1_700_000_000,
                search_snippet: "A longer deploy excerpt with surrounding context.",
              },
              {
                id: "archived",
                title: "Old deploy",
                labels: {},
                agent_name: "coding",
                archived: true,
                updated_at: 1_600_000_000,
                search_snippet: "Archived deploy discussion.",
              },
            ],
          },
        ],
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
    });

    render(
      <MemoryRouter initialEntries={["/search?q=deploy"]}>
        <SearchPage />
      </MemoryRouter>,
    );

    expect(useConversations).toHaveBeenCalledWith("deploy", true);
    expect(screen.getByRole("main").parentElement).toHaveClass("max-w-none");
    expect(screen.getByText("Archived")).toBeInTheDocument();
    expect(screen.getAllByText(/deploy/i).length).toBeGreaterThan(1);
    for (const excerpt of screen.getAllByTestId("search-result-excerpt")) {
      expect(excerpt).toHaveClass("line-clamp-6");
    }
  });
});
