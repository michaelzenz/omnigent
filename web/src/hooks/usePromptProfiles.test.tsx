import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  useCreatePromptProfile,
  usePromptProfiles,
  useUpdatePromptProfile,
} from "./usePromptProfiles";

const fetchMock = vi.fn();

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response;
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("prompt profile API hooks", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("loads only selectable profiles when requested", async () => {
    fetchMock.mockResolvedValue(
      response({
        data: [
          {
            id: "profile_team",
            name: "Team",
            description: "Coordinate the work",
            instructions: "Delegate carefully",
            enabled: true,
            created_at: 1,
            updated_at: 2,
          },
        ],
      }),
    );

    const { result } = renderHook(() => usePromptProfiles({ enabledOnly: true }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/prompt-profiles?enabled_only=true",
      expect.any(Object),
    );
    expect(result.current.data?.[0]?.name).toBe("Team");
  });

  it("creates profiles from JSON without an agent bundle", async () => {
    fetchMock.mockResolvedValue(response({ id: "profile_new" }));
    const { result } = renderHook(() => useCreatePromptProfile(), { wrapper });

    await act(() =>
      result.current.mutateAsync({
        name: "Research",
        description: "Investigate",
        instructions: "Cite sources",
        enabled: true,
      }),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/prompt-profiles",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "Research",
          description: "Investigate",
          instructions: "Cite sources",
          enabled: true,
        }),
      }),
    );
  });

  it("patches only provided profile fields", async () => {
    fetchMock.mockResolvedValue(response({ id: "profile_team" }));
    const { result } = renderHook(() => useUpdatePromptProfile(), { wrapper });

    await act(() => result.current.mutateAsync({ id: "profile_team", enabled: false }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/prompt-profiles/profile_team",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ enabled: false }),
      }),
    );
  });
});
