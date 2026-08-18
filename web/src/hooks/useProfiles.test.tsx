import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { autoSelectProfile, useEditProfile, useProfiles } from "./useProfiles";

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

describe("profile API hooks", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("loads disabled profiles and maps management metadata", async () => {
    fetchMock.mockResolvedValue(
      response({
        data: [
          {
            id: "ag_team",
            name: "team",
            description: "A coordinated team",
            harness: "claude-sdk",
            builtin: false,
            enabled: false,
            archived: false,
            is_multi_agent: true,
            subagent_count: 3,
            default_harness: "codex",
            default_model: "gpt-5",
          },
        ],
        has_more: false,
      }),
    );

    const { result } = renderHook(() => useProfiles(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents?include_disabled=true&limit=1000",
      expect.any(Object),
    );
    expect(result.current.data?.[0]).toMatchObject({
      id: "ag_team",
      enabled: false,
      is_multi_agent: true,
      subagent_count: 3,
      default_harness: "codex",
      default_model: "gpt-5",
    });
  });

  it("posts the draft input and returns the selected profile", async () => {
    fetchMock.mockResolvedValue(
      response({
        profile: {
          id: "ag_team",
          name: "team",
          harness: "claude-sdk",
          enabled: true,
        },
        reason: null,
      }),
    );

    const selected = await autoSelectProfile("investigate the outage");
    expect(selected.id).toBe("ag_team");
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents/auto-select",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input: "investigate the outage" }),
      }),
    );
  });

  it("puts edited prompt fields without replacing capabilities client-side", async () => {
    fetchMock.mockResolvedValue(
      response({
        id: "ag_team",
        name: "edited-team",
        description: "Updated",
        instructions: "New prompt",
        harness: "claude-sdk",
        enabled: true,
      }),
    );
    const { result } = renderHook(() => useEditProfile(), { wrapper });

    await act(() =>
      result.current.mutateAsync({
        id: "ag_team",
        name: "edited-team",
        description: "Updated",
        instructions: "New prompt",
      }),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agents/ag_team",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          name: "edited-team",
          description: "Updated",
          instructions: "New prompt",
        }),
      }),
    );
  });
});
