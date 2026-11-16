import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RolesTab } from "./RolesTab";
import type {
  RoleProfile,
  RoleProfileChanges,
  RoleScheduleSettings,
} from "@/hooks/useGlobalHubRoleProfiles";
import { authenticatedFetch } from "@/lib/identity";
import type * as IdentityModule from "@/lib/identity";
import { updateSession } from "@/lib/sessionsApi";
import type * as SessionsApiModule from "@/lib/sessionsApi";
import { localTimezone } from "@/lib/timezones";
import { jsonResponse } from "@/test/globalHubFixtures";

vi.mock("@/lib/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof IdentityModule>()),
  authenticatedFetch: vi.fn(),
}));
vi.mock("@/lib/sessionsApi", async (importOriginal) => ({
  ...(await importOriginal<typeof SessionsApiModule>()),
  updateSession: vi.fn(),
}));
vi.mock("@/hooks/useGlobalHubSettings", () => ({
  useGlobalHubSettings: () => ({ data: { host_id: "h1", workspace: "/srv" } }),
}));
vi.mock("@/hooks/useHosts", () => ({
  useHostModelOptions: () => ({ data: [{ id: "model-a", displayName: "Model A" }] }),
}));
// Radix Select uses a portal and pointer events that jsdom cannot drive.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string;
    onValueChange: (value: string) => void;
    children: ReactNode;
  }) => (
    <select value={value} onChange={(event) => onValueChange(event.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => children,
  SelectItem: ({ value, children }: { value: string; children: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}));

const fetchMock = vi.mocked(authenticatedFetch);
const TITLES: Record<string, string> = {
  broker: "Task broker",
  custodian: "Task custodian",
  "manager:default": "Task manager",
};
const THREE_HOURS_S = 3 * 60 * 60;

function profile(role: string, fields: Partial<RoleProfile> = {}): RoleProfile {
  return {
    role,
    kind: role.split(":")[0],
    title: TITLES[role],
    description: "Default description",
    model: null,
    customized: [],
    manual: "Default manual",
    default_description: "Default description",
    default_manual: "Default manual",
    schedulable: role === "custodian",
    schedule: null,
    ...fields,
  };
}

/** The saved form of a schedule: an enabled one runs next in three hours. */
function savedSchedule(schedule: RoleScheduleSettings | null): RoleProfile["schedule"] {
  if (schedule === null) return null;
  const nextRunAt = Math.floor(Date.now() / 1000) + THREE_HOURS_S;
  return { ...schedule, next_run_at: schedule.enabled ? nextRunAt : null };
}

/** Serve the roles; a PUT saves its changes, as the server does. */
function serveRoles(profiles: RoleProfile[]): void {
  const byRole = new Map(profiles.map((saved) => [saved.role, saved]));
  fetchMock.mockImplementation(async (input, init) => {
    const path = decodeURIComponent(String(input));
    if (path === "/v1/agent-tasks/roles/profiles")
      return jsonResponse({ data: [...byRole.values()] });
    if (path === "/v1/agent-tasks/watchers") return jsonResponse({ data: [] });
    if (path === "/v1/agent-tasks/board/roles") {
      return jsonResponse({
        data: [
          { role: "broker", title: "Task broker", session_id: "s-broker" },
          { role: "manager:default", title: "Storage", session_id: "s-storage" },
          { role: "manager:default", title: "Docs", session_id: "s-docs" },
          { role: "manager:default", title: "No session", session_id: null },
        ],
      });
    }
    const role = path.match(/^\/v1\/agent-tasks\/roles\/(.+)\/profile$/)?.[1];
    const saved = role === undefined ? undefined : byRole.get(role);
    if (saved === undefined) return jsonResponse({ detail: "Not found" }, 404);
    if (init?.method !== "PUT") return jsonResponse(saved);
    const { schedule, ...changes } = JSON.parse(String(init.body)) as RoleProfileChanges;
    const next: RoleProfile = {
      ...saved,
      ...Object.fromEntries(
        Object.entries(changes).map(([field, value]) => [
          field,
          value ?? (field === "model" ? null : saved[`default_${field}` as keyof RoleProfile]),
        ]),
      ),
      ...(schedule === undefined ? {} : { schedule: savedSchedule(schedule) }),
    };
    byRole.set(saved.role, next);
    return jsonResponse(next);
  });
}

function renderTab(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RolesTab />
    </QueryClientProvider>,
  );
}

/** The bodies of the PUT requests the tab sent. */
function savedBodies(): unknown[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "PUT")
    .map(([, init]) => JSON.parse(String(init?.body)));
}

async function card(role: string) {
  return within(await screen.findByTestId(`role-card-${role}`));
}

beforeEach(() => {
  vi.mocked(updateSession).mockResolvedValue({} as Awaited<ReturnType<typeof updateSession>>);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RolesTab", () => {
  it("saves only the fields that changed, and Cancel puts back the saved values", async () => {
    serveRoles([profile("broker")]);
    renderTab();
    const broker = await card("broker");
    const description = await broker.findByLabelText("Description");

    fireEvent.change(description, { target: { value: "Routes events." } });
    fireEvent.click(broker.getByRole("button", { name: "Cancel" }));
    expect(await broker.findByLabelText("Description")).toHaveValue("Default description");

    fireEvent.change(broker.getByLabelText("Description"), {
      target: { value: "Routes events." },
    });
    fireEvent.click(broker.getByRole("button", { name: "Save" }));

    expect(await broker.findByText("Saved.")).toBeInTheDocument();
    expect(savedBodies()).toEqual([{ description: "Routes events." }]);
    expect(broker.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(updateSession).not.toHaveBeenCalled();
  });

  it("resets a customized manual to its default, which saves as null", async () => {
    serveRoles([profile("broker", { manual: "My manual", customized: ["manual"] })]);
    renderTab();
    const broker = await card("broker");

    expect(await broker.findByText("Customized")).toBeInTheDocument();
    fireEvent.click(broker.getByRole("button", { name: "Reset to default" }));
    expect(broker.getByLabelText("Manual")).toHaveValue("Default manual");
    fireEvent.click(broker.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(savedBodies()).toEqual([{ manual: null }]));
  });

  it("switches the role's running sessions to a new model", async () => {
    serveRoles([profile("broker"), profile("manager:default")]);
    vi.mocked(updateSession).mockImplementation(async (sessionId) => {
      if (sessionId === "s-docs") throw new Error("host offline");
      return {} as Awaited<ReturnType<typeof updateSession>>;
    });
    renderTab();
    const manager = await card("manager:default");

    fireEvent.change(await manager.findByRole("combobox"), { target: { value: "model-a" } });
    fireEvent.click(manager.getByRole("button", { name: "Save" }));

    expect(
      await manager.findByText(
        "Saved. 1 running session switched to the new model. Could not switch: Docs.",
      ),
    ).toBeInTheDocument();
    expect(savedBodies()).toEqual([{ model: "model-a" }]);
    expect(vi.mocked(updateSession).mock.calls).toEqual([
      ["s-storage", { modelOverride: "model-a" }],
      ["s-docs", { modelOverride: "model-a" }],
    ]);
  });

  it("turns on the custodian's schedule, every day at 09:00 in the browser's timezone", async () => {
    serveRoles([profile("broker"), profile("custodian")]);
    renderTab();
    const broker = await card("broker");
    const custodian = await card("custodian");
    const scheduleSwitch = await custodian.findByRole("switch", { name: "Schedule" });

    // Only a schedulable role offers a schedule.
    expect(broker.queryByRole("switch")).not.toBeInTheDocument();
    expect(scheduleSwitch).not.toBeChecked();
    expect(custodian.queryByTestId("schedule-fields")).not.toBeInTheDocument();
    expect(custodian.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(scheduleSwitch);
    expect(custodian.getByTestId("schedule-fields")).toBeInTheDocument();
    fireEvent.click(custodian.getByRole("button", { name: "Save" }));

    expect(await custodian.findByText("Next run in 3 hours.")).toBeInTheDocument();
    expect(savedBodies()).toEqual([
      {
        schedule: {
          enabled: true,
          rrule: "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
          timezone: localTimezone(),
        },
      },
    ]);
  });

  it("turns off a saved schedule and keeps its rule and timezone", async () => {
    const schedule = { enabled: true, rrule: "FREQ=HOURLY;BYMINUTE=30", timezone: "Europe/Berlin" };
    serveRoles([profile("custodian", { schedule: savedSchedule(schedule) })]);
    renderTab();
    const custodian = await card("custodian");
    const scheduleSwitch = await custodian.findByRole("switch", { name: "Schedule" });

    expect(scheduleSwitch).toBeChecked();
    expect(custodian.getByTestId("schedule-fields")).toBeInTheDocument();
    expect(custodian.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(scheduleSwitch);
    fireEvent.click(custodian.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(savedBodies()).toEqual([{ schedule: { ...schedule, enabled: false } }]),
    );
    expect(await custodian.findByRole("switch", { name: "Schedule" })).not.toBeChecked();
    expect(custodian.queryByTestId("role-schedule-next-run")).not.toBeInTheDocument();
  });
});
