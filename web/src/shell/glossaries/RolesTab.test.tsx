import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RolesTab } from "./RolesTab";

vi.mock("./RoleDefaultsForm", () => ({
  RoleDefaultsForm: ({ roleId }: { roleId: string }) => (
    <div data-testid={`glossary-role-defaults-${roleId}`} />
  ),
}));

vi.mock("./TemplateRolesSection", () => ({
  TemplateRolesSection: ({ testId }: { testId: string }) => <div data-testid={testId} />,
}));

describe("RolesTab", () => {
  it("renders broker, secretary, and template role sections", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <RolesTab />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("glossary-role-card-broker")).toBeInTheDocument();
    expect(screen.getByTestId("glossary-role-card-secretary")).toBeInTheDocument();
    expect(screen.getByTestId("glossary-role-defaults-broker")).toBeInTheDocument();
    expect(screen.getByTestId("glossary-role-defaults-secretary")).toBeInTheDocument();
    expect(screen.getByTestId("glossary-manager-roles-section")).toBeInTheDocument();
    expect(screen.getByTestId("glossary-worker-roles-section")).toBeInTheDocument();
  });
});
