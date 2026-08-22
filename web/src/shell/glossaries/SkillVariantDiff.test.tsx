import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  props: null as {
    original?: string;
    modified?: string;
    options?: { renderSideBySide?: boolean; ignoreTrimWhitespace?: boolean };
  } | null,
}));

vi.mock("@monaco-editor/react", () => ({
  DiffEditor: (props: {
    original?: string;
    modified?: string;
    options?: { renderSideBySide?: boolean; ignoreTrimWhitespace?: boolean };
  }) => {
    h.props = props;
    return <div data-testid="diff-editor" />;
  },
}));
vi.mock("../monacoSetup", () => ({
  ensureMonacoReady: vi.fn(() => Promise.resolve()),
  ensureLanguage: vi.fn(() => Promise.resolve()),
  monacoLanguageId: vi.fn((language: string) => language),
  resolvedThemeToMonaco: vi.fn(() => "github-light"),
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));

import { SkillVariantDiff } from "./SkillVariantDiff";

beforeEach(() => {
  h.props = null;
});

afterEach(() => {
  cleanup();
});

describe("SkillVariantDiff", () => {
  it("shows additions and deletions from comparison variant to selected variant", async () => {
    render(
      <SkillVariantDiff
        original={"old line\n"}
        modified={"new line\n"}
        originalLabel="Variant 2"
        modifiedLabel="Variant 1"
      />,
    );

    await waitFor(() => expect(screen.getByTestId("diff-editor")).toBeInTheDocument());
    expect(h.props?.original).toBe("old line\n");
    expect(h.props?.modified).toBe("new line\n");
    expect(h.props?.options?.ignoreTrimWhitespace).toBe(false);
    expect(screen.getByText("Removed")).toBeInTheDocument();
    expect(screen.getByText("Added")).toBeInTheDocument();
    expect(screen.getByText("Variant 2 → Variant 1")).toBeInTheDocument();
  });

  it("switches between split and unified highlighting", async () => {
    render(
      <SkillVariantDiff
        original="old"
        modified="new"
        originalLabel="Variant 2"
        modifiedLabel="Variant 1"
      />,
    );
    await waitFor(() => expect(h.props?.options?.renderSideBySide).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Unified" }));

    await waitFor(() => expect(h.props?.options?.renderSideBySide).toBe(false));
  });
});
