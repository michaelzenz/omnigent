import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { GlossariesPage } from "./GlossariesPage";

vi.mock("@/shell/glossaries/RolesTab", () => ({
  RolesTab: () => <div data-testid="glossaries-roles-tab" />,
}));

vi.mock("@/shell/glossaries/SkillsTab", () => ({
  SkillsTab: () => <div data-testid="glossaries-skills-tab" />,
}));

vi.mock("@/shell/glossaries/MemoryTab", () => ({
  MemoryTab: () => <div data-testid="glossaries-memory-tab" />,
}));

describe("GlossariesPage", () => {
  it("renders glossaries shell with roles tab by default", () => {
    render(
      <MemoryRouter initialEntries={["/glossaries"]}>
        <GlossariesPage />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("glossaries-page")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Roles" })).toBeInTheDocument();
    expect(screen.getByTestId("glossaries-roles-tab")).toBeInTheDocument();
  });

  it("renders the skills tab when tab=skills", () => {
    render(
      <MemoryRouter initialEntries={["/glossaries?tab=skills"]}>
        <GlossariesPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("tab", { name: "Skills" })).toBeInTheDocument();
    expect(screen.getByTestId("glossaries-skills-tab")).toBeInTheDocument();
  });

  it("renders the memory tab immediately after skills when tab=memory", () => {
    render(
      <MemoryRouter initialEntries={["/glossaries?tab=memory"]}>
        <GlossariesPage />
      </MemoryRouter>,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Roles",
      "Pollers",
      "Timers",
      "Skills",
      "Memory",
    ]);
    expect(screen.getByTestId("glossaries-memory-tab")).toBeInTheDocument();
  });
});
