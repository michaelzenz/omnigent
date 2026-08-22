import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Bubble } from "@/lib/renderItems";
import { FileViewerContext } from "@/shell/FileViewerContext";
import { useChatStore } from "@/store/chatStore";
import {
  BubbleView,
  nearestCrossedUserMessageId,
  SessionRewindContext,
  userMessageIndexNearestRoof,
} from "./ChatPage";

// UserBubble renders its text through the same markdown renderer as the
// assistant bubble (FilePathAwareMessageResponse → Streamdown). These tests
// pin that wiring: if the text path reverts to a raw `{text}` string, the
// markdown syntax would render literally and these assertions would fail.

afterEach(cleanup);

const FILE_VIEWER_NOOP = {
  openFile: () => {},
  isChangedPath: () => false,
  conversationId: undefined,
  workspaceRoot: null,
  workspaceHome: null,
};

function userBubble(text: string, overrides: Partial<Extract<Bubble, { kind: "user" }>> = {}) {
  return {
    kind: "user" as const,
    itemId: "u1",
    content: [{ type: "input_text" as const, text }],
    ...overrides,
  };
}

function assistantBubble(
  lifecycle: Extract<Bubble, { kind: "assistant" }>["lifecycle"],
  text = "partial answer",
): Extract<Bubble, { kind: "assistant" }> {
  return {
    kind: "assistant",
    responseId: "codex_turn_123",
    stableId: "msg_1",
    lifecycle,
    error: null,
    items: [{ kind: "text", itemId: "msg_1", text, final: true }],
  };
}

function renderBubble(bubble: Bubble) {
  return render(
    <FileViewerContext.Provider value={FILE_VIEWER_NOOP}>
      <BubbleView bubble={bubble} />
    </FileViewerContext.Provider>,
  );
}

function renderEditableBubble(
  bubble: Bubble,
  isStickyUser = false,
  stickyUserMessagesEnabled = true,
) {
  return render(
    <FileViewerContext.Provider value={FILE_VIEWER_NOOP}>
      <SessionRewindContext.Provider value>
        <BubbleView
          bubble={bubble}
          isStickyUser={isStickyUser}
          stickyUserMessagesEnabled={stickyUserMessagesEnabled}
        />
      </SessionRewindContext.Provider>
    </FileViewerContext.Provider>,
  );
}

describe("UserBubble markdown rendering", () => {
  it("renders **bold** markdown as a strong node, not literal asterisks", () => {
    renderBubble(userBubble("hello **world**"));
    // Streamdown emits bold as an element tagged data-streamdown="strong"
    // (a <span class="font-semibold">, not a literal <strong>). Finding it
    // proves the inline markdown parser ran; a raw-text path would have no
    // such node.
    const bolded = screen.getByText("world");
    expect(bolded.getAttribute("data-streamdown")).toBe("strong");
    // The literal markdown source must NOT survive as text.
    expect(screen.queryByText(/\*\*world\*\*/)).toBeNull();
  });

  it("renders a markdown list as <li> items", async () => {
    renderBubble(userBubble("- first\n- second"));
    // Two list items prove the markdown block parser ran. A raw-text path
    // would render the source as a single line with literal hyphens.
    const first = await screen.findByText("first", { selector: "li, li *" });
    const second = await screen.findByText("second", { selector: "li, li *" });
    expect(first.closest("li")).not.toBeNull();
    expect(second.closest("li")).not.toBeNull();
  });

  it("renders fenced code blocks inside a <pre> wrapper", async () => {
    renderBubble(userBubble("```python\ndef foo():\n    return 1\n```\n"));
    // Mirrors the assistant-side guarantee: fenced code keeps its <pre>
    // wrapper rather than collapsing to inline text.
    const pre = await screen.findByText(/def foo/, { selector: "pre, pre *" });
    expect(pre.closest("pre")).not.toBeNull();
  });

  it("keeps single newlines as <br> line breaks (remark-breaks)", () => {
    const { container } = renderBubble(userBubble("line one\nline two"));
    // The `breaks` prop appends remark-breaks, so a single newline becomes a
    // hard <br>. Without it, CommonMark would collapse the newline to a space
    // and this query would find no <br>. Both lines live in one paragraph.
    expect(container.querySelectorAll("br").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/line one/)).toBeDefined();
    expect(screen.getByText(/line two/)).toBeDefined();
  });

  it("still renders GFM tables — remark-breaks extends, not replaces, the defaults", async () => {
    renderBubble(userBubble("| a | b |\n| - | - |\n| 1 | 2 |"));
    // The regression guard for the extend-not-replace decision: if we had
    // passed [remarkBreaks] alone, Streamdown would drop remark-gfm and this
    // table would render as literal pipe text with no <table>/<td>.
    const cell = await screen.findByText("1", { selector: "td, td *" });
    expect(cell.closest("table")).not.toBeNull();
  });

  it("renders CJK text around explicit inline math", async () => {
    const { container } = renderBubble(userBubble(String.raw`中文 \(\sqrt{x + 1}\) 文本`));

    await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
    expect(container.textContent).toContain("中文");
    expect(container.textContent).toContain("文本");
    const katex = container.querySelector(".katex") as HTMLElement;
    expect(katex.querySelector(".sqrt")).not.toBeNull();
    expect(katex.textContent).toContain("x");
    expect(katex.textContent).toContain("1");
  });
});

describe("UserBubble system messages", () => {
  it("keeps hook order stable when a system message becomes a regular message", () => {
    const { rerender } = renderBubble(userBubble("[System: timer build fired]"));
    expect(screen.getByTestId("system-message")).toBeInTheDocument();

    rerender(
      <FileViewerContext.Provider value={FILE_VIEWER_NOOP}>
        <BubbleView bubble={userBubble("build finished")} />
      </FileViewerContext.Provider>,
    );

    expect(screen.queryByTestId("system-message")).toBeNull();
    expect(screen.getByText("build finished")).toBeInTheDocument();
  });

  it("renders a steering interrupt as a muted marker and its uploads as their own bubble", () => {
    // The two items a mid-tool-use steer produces, once the pending-input
    // drain stops handing the uploads to the marker: Claude's own interrupt
    // record (text-only) and the user's attachments-only message.
    renderBubble(userBubble("[Request interrupted by user for tool use]"));
    const marker = screen.getByTestId("system-message");
    expect(marker.getAttribute("data-system-kind")).toBe("interrupted");
    expect(marker).toHaveTextContent("Interrupted");
    // The raw record must not survive as user-bubble text.
    expect(screen.queryByText(/\[Request interrupted by user/)).toBeNull();
    expect(screen.queryByTestId("message-bubble")).toBeNull();

    cleanup();

    renderBubble(
      userBubble("[Attached: /tmp/uploads/shot1.png]\n\n[Attached: /tmp/uploads/shot2.png]", {
        content: [
          { type: "input_image", file_id: "file_1", filename: "shot1.png" },
          { type: "input_image", file_id: "file_2", filename: "shot2.png" },
          {
            type: "input_text",
            text: "[Attached: /tmp/uploads/shot1.png]\n\n[Attached: /tmp/uploads/shot2.png]",
          },
        ],
      }),
    );
    // A real bubble with both screenshots — not the blank pill the stolen
    // file blocks used to leave behind.
    expect(screen.getByTestId("message-bubble")).toBeInTheDocument();
    expect(screen.getByAltText("shot1.png")).toBeInTheDocument();
    expect(screen.getByAltText("shot2.png")).toBeInTheDocument();
    // Upload markers are stripped from the text, and an empty text renders
    // nothing rather than an empty markdown block.
    expect(screen.queryByText(/\[Attached:/)).toBeNull();
    expect(screen.queryByTestId("system-message")).toBeNull();
  });
});

describe("AssistantBubble lifecycle rendering", () => {
  it("shows an interrupted indicator for cancelled assistant bubbles", () => {
    renderBubble(assistantBubble("cancelled"));

    expect(screen.getByTestId("assistant-interrupted-indicator")).toHaveTextContent("Interrupted");
  });

  it("does not show an interrupted indicator for completed assistant bubbles", () => {
    renderBubble(assistantBubble("completed"));

    expect(screen.queryByTestId("assistant-interrupted-indicator")).toBeNull();
  });
});

describe("UserBubble copy button", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses a compact action button with an 8px content gap", () => {
    renderBubble(userBubble("copy me please"));

    expect(screen.getByTestId("message-bubble")).toHaveClass("gap-2");
    expect(screen.getByRole("button", { name: "Copy" })).toHaveAttribute("data-size", "icon-xxs");
  });

  it("shows the full-row background and actions only while hovered", () => {
    renderBubble(userBubble("hover me"));
    const bubble = screen.getByTestId("message-bubble");
    const actions = screen.getByTestId("user-message-actions");

    expect(bubble).not.toHaveClass("bg-background/95");
    expect(actions).toHaveClass("md:opacity-0");
    fireEvent.pointerEnter(bubble);
    expect(bubble).toHaveClass("bg-background/95", "backdrop-blur-md");
    expect(actions).toHaveClass("md:opacity-100");
    fireEvent.pointerLeave(bubble);
    expect(bubble).not.toHaveClass("bg-background/95");
  });

  it("copies the message text to the clipboard when clicked", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    renderBubble(userBubble("copy me please"));

    fireEvent.click(screen.getByRole("button", { name: "Copy" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("copy me please"));
  });

  it("falls back to execCommand when the async clipboard is unavailable", async () => {
    // The iOS webview / non-secure origins expose no navigator.clipboard, which
    // is exactly where the old direct-writeText guard made this button a silent
    // no-op. copyText() must fall through to the execCommand path instead.
    vi.stubGlobal("navigator", {});
    const realExecCommand = document.execCommand;
    const execCommand = vi.fn().mockReturnValue(true);
    document.execCommand = execCommand;

    try {
      renderBubble(userBubble("copy via fallback"));
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await waitFor(() => expect(execCommand).toHaveBeenCalledWith("copy"));
    } finally {
      document.execCommand = realExecCommand;
    }
  });

  it("shows a toast confirmation on a mobile viewport", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const real = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: /max-width/.test(query),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
    const onToast = vi.fn();
    window.addEventListener("omnigent:toast", onToast);

    try {
      renderBubble(userBubble("copy me please"));
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
      await waitFor(() => expect(onToast).toHaveBeenCalled());
    } finally {
      window.removeEventListener("omnigent:toast", onToast);
      window.matchMedia = real;
    }
  });

  it("jumps to the message start and clears hover chrome", () => {
    renderBubble(userBubble("jump me"));
    const bubble = screen.getByTestId("message-bubble");
    bubble.style.position = "sticky";
    const scrollIntoView = vi.fn(() => {
      expect(bubble.style.position).toBe("static");
    });
    bubble.scrollIntoView = scrollIntoView;
    fireEvent.pointerEnter(bubble);

    const jump = screen.getByRole("button", { name: "Jump to turn start" });
    jump.focus();
    fireEvent.click(jump);

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "auto" });
    expect(bubble).not.toHaveClass("bg-background/95");
    expect(document.activeElement).not.toBe(jump);
  });

  it("aligns a sticky message with its roof so the response remains visible", () => {
    render(
      <div data-testid="scroll-root" style={{ overflowY: "auto" }}>
        <FileViewerContext.Provider value={FILE_VIEWER_NOOP}>
          <BubbleView bubble={userBubble("jump me")} />
        </FileViewerContext.Provider>
      </div>,
    );
    const scroller = screen.getByTestId("scroll-root");
    let scrollTop = 900;
    Object.defineProperties(scroller, {
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => {
          scrollTop = value;
        },
      },
      scrollHeight: { configurable: true, get: () => 2000 },
      clientHeight: { configurable: true, get: () => 500 },
    });
    vi.spyOn(scroller, "getBoundingClientRect").mockReturnValue({ top: 0 } as DOMRect);

    const bubble = screen.getByTestId("message-bubble");
    bubble.style.position = "sticky";
    bubble.style.top = "80px";
    vi.spyOn(bubble, "getBoundingClientRect").mockImplementation(
      () => ({ top: bubble.style.position === "static" ? -300 : 80 }) as DOMRect,
    );
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      callback(performance.now() + 600);
      return 1;
    });

    fireEvent.pointerEnter(bubble);
    fireEvent.click(screen.getByRole("button", { name: "Jump to turn start" }));

    expect(scrollTop).toBe(520);
    expect(bubble.style.position).toBe("sticky");
    vi.restoreAllMocks();
  });

  it("does not render a copy button for an attachments-only message (no text)", () => {
    renderBubble(
      userBubble("", {
        content: [{ type: "input_image", file_id: "f1", filename: "a.png" }],
      }),
    );
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
  });
});

describe("UserBubble execution summary", () => {
  it("shows the used profile and harness/model left of the timestamp", () => {
    renderBubble(
      userBubble("hello", {
        createdAtS: 1_700_000_000,
        executionContext: {
          profile: "research",
          harness: "omniharness",
          model: "databricks-gpt-5-5",
        },
      }),
    );

    const summary = screen.getByTestId("message-execution-summary");
    const timestamp = screen.getByTestId("message-timestamp");
    expect(summary.textContent).toBe("Profile: Research · omniharness / databricks-gpt-5-5");
    expect(summary.parentElement).toBe(timestamp.parentElement);
    expect(summary.compareDocumentPosition(timestamp) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(
      0,
    );
  });

  it("shows every included profile when the execution line remains short", () => {
    renderBubble(
      userBubble("hello", {
        executionContext: {
          profiles: ["research", "review"],
          harness: "omniharness",
          model: "glm",
        },
      }),
    );

    expect(screen.getByTestId("message-execution-summary")).toHaveTextContent(
      "Profile: Research, Review · omniharness / glm",
    );
  });

  it("collapses long included-profile lists to Multiple and preserves the tooltip", () => {
    renderBubble(
      userBubble("hello", {
        executionContext: {
          profiles: [
            "managed table migration specialist",
            "predictive optimization reviewer",
            "storage architecture analyst",
          ],
          harness: "omniharness",
          model: "databricks-gpt-5-6-luna",
        },
      }),
    );

    const summary = screen.getByTestId("message-execution-summary");
    expect(summary).toHaveTextContent(
      "Profile: Multiple · omniharness / databricks-gpt-5-6-luna",
    );
    expect(summary.title).toContain("Managed Table Migration Specialist");
    expect(summary.title).toContain("Predictive Optimization Reviewer");
  });
});

describe("UserBubble rewind editor", () => {
  it("opens by clicking the sent text and cancels locally without rewinding", () => {
    const rewindAndSend = vi.fn();
    useChatStore.setState({
      conversationId: "conv_1",
      sessionHarness: "openai-agents",
      boundAgentId: "agent_1",
      rewindAndSend,
    });
    renderEditableBubble(userBubble("edit me"));

    expect(screen.queryByRole("button", { name: "Edit and rewind" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit sent message" }));

    expect(screen.getByTestId("rewind-message-editor")).toHaveValue("edit me");
    expect(rewindAndSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("rewind-message-editor")).toBeNull();
    expect(screen.getByText("edit me")).toBeInTheDocument();
  });

  it("clamps a sent message outside sticky mode and expands it into a large editor", () => {
    useChatStore.setState({
      conversationId: "conv_1",
      sessionHarness: "openai-agents",
      boundAgentId: "agent_1",
      rewindAndSend: vi.fn(),
    });
    renderEditableBubble(userBubble("line one\nline two\nline three\nline four"));

    const message = screen.getByTestId("editable-user-message");
    expect(screen.getByTestId("user-message-text")).toHaveClass("line-clamp-6");
    expect(screen.getByTestId("sent-message-edit-icon")).toBeInTheDocument();
    expect(screen.getByTestId("message-bubble")).not.toHaveClass("sticky");
    fireEvent.click(message);

    const editor = screen.getByTestId("rewind-message-editor");
    expect(editor).toHaveValue("line one\nline two\nline three\nline four");
    expect(editor).toHaveClass("max-h-[70vh]", "resize-y");
    expect(editor.closest("form")).toHaveClass("w-full");
    expect(editor.closest("form")).not.toHaveClass("max-w-[640px]");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("keeps the selected sent message pinned while collapsed", () => {
    useChatStore.setState({
      conversationId: "conv_1",
      sessionHarness: "openai-agents",
      boundAgentId: "agent_1",
      rewindAndSend: vi.fn(),
    });
    renderEditableBubble(userBubble("sticky text"), true);

    expect(screen.getByTestId("message-bubble")).toHaveClass("sticky");
    expect(screen.getByTestId("user-message-text")).toHaveClass("line-clamp-6");
  });

  it("keeps click-to-edit but disables clamping and pinning with the preference off", () => {
    useChatStore.setState({
      conversationId: "conv_1",
      sessionHarness: "openai-agents",
      boundAgentId: "agent_1",
      rewindAndSend: vi.fn(),
    });
    renderEditableBubble(userBubble("full message"), true, false);

    expect(screen.getByTestId("message-bubble")).not.toHaveClass("sticky");
    expect(screen.getByTestId("user-message-text")).not.toHaveClass("line-clamp-6");
    expect(screen.getByTestId("sent-message-edit-icon")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit sent message" }));
    expect(screen.getByTestId("rewind-message-editor")).toHaveValue("full message");
  });
});

describe("sticky user turn selection", () => {
  it("keeps the nearest crossed turn pinned until the next reaches the roof", () => {
    const messages = [
      { itemId: "first", top: 40 },
      { itemId: "second", top: 120 },
      { itemId: "third", top: 220 },
    ];

    expect(nearestCrossedUserMessageId(messages, 80)).toBe("first");
    expect(nearestCrossedUserMessageId(messages, 120)).toBe("second");
  });
});

describe("previous user turn selection", () => {
  const messages = [{ top: 100 }, { top: 500 }, { top: 1_400 }];

  it("selects the current long bubble when its top is nearest above the roof", () => {
    expect(userMessageIndexNearestRoof(messages, 1_100)).toBe(1);
  });

  it("selects the preceding bubble when the current bubble is already aligned", () => {
    expect(userMessageIndexNearestRoof(messages, 500)).toBe(0);
  });

  it("selects the latest crossed bubble while viewing its reply", () => {
    expect(userMessageIndexNearestRoof(messages, 1_800)).toBe(2);
  });
});

describe("UserBubble @-mention attachment chips", () => {
  it("shows file and folder chips from [Attached: …] markers and hides the markers", () => {
    renderBubble(userBubble("[Attached: src/server.ts]\n[Attached: docs/]\n\nsummarize these"));

    // The marker paths surface as chips (folder keeps its trailing slash)...
    expect(screen.getByText("@src/server.ts")).toBeInTheDocument();
    expect(screen.getByText("@docs/")).toBeInTheDocument();
    // ...while the raw "[Attached: …]" marker text is stripped from the body.
    expect(screen.queryByText(/\[Attached:/)).toBeNull();
    expect(screen.getByText("summarize these")).toBeInTheDocument();
  });

  it("renders chips for the codex 'Attached file:' wording too", () => {
    renderBubble(userBubble("[Attached file: src/a.ts]\n\ncheck this"));
    expect(screen.getByText("@src/a.ts")).toBeInTheDocument();
    expect(screen.queryByText(/\[Attached/)).toBeNull();
  });

  it("shows the line span of a partial-file attach in its own (non-truncating) node", () => {
    renderBubble(userBubble("[Attached: bob-max-gain/docker-compose.yml:2-9]\n\nreview"));
    expect(screen.getByText("@bob-max-gain/docker-compose.yml")).toBeInTheDocument();
    expect(screen.getByText(":2-9")).toBeInTheDocument();
  });

  // An explicit upload is materialized to disk by the native executor, which
  // injects an *absolute* "[Attached: <bridge>/uploads/…]" marker for the CLI.
  // The upload already rides in as an input_image / input_file block, so the
  // marker must NOT also surface as a path chip (it would double-render, and
  // the path is an internal temp dir).
  it("does not chip an absolute upload marker (already shown via its file block)", () => {
    renderBubble(
      userBubble(
        "[Attached: /var/folders/x/omnigent-1/claude-native/abc/uploads/image.png]\n\nwhat is this",
      ),
    );
    // No "@…" chip for the absolute upload path.
    expect(screen.queryByText(/^@\//)).toBeNull();
    expect(screen.queryByText(/uploads\/image\.png/)).toBeNull();
    // The marker is still stripped from the body and the prose survives.
    expect(screen.queryByText(/\[Attached:/)).toBeNull();
    expect(screen.getByText("what is this")).toBeInTheDocument();
  });

  // The absolute-path heuristic is OS-agnostic so it still suppresses the
  // chip if an executor ever materializes an upload on a Windows host
  // (drive-letter or UNC root), where the marker wouldn't start with "/".
  it.each([
    ["C:\\Users\\me\\AppData\\Local\\Temp\\omnigent\\uploads\\image.png", "drive (backslash)"],
    ["C:/Users/me/AppData/Local/Temp/omnigent/uploads/image.png", "drive (forward slash)"],
    ["\\\\host\\share\\omnigent\\uploads\\image.png", "UNC"],
  ])("does not chip a Windows-style absolute upload marker (%s)", (path) => {
    renderBubble(userBubble(`[Attached: ${path}]\n\nwhat is this`));
    expect(screen.queryByText(/uploads/)).toBeNull();
    expect(screen.getByText("what is this")).toBeInTheDocument();
  });

  it("chips a relative @-mention but not an absolute upload in the same message", () => {
    renderBubble(
      userBubble(
        "[Attached: /tmp/omnigent/claude-native/abc/uploads/image.png]\n" +
          "[Attached: src/server.ts]\n\ncompare",
      ),
    );
    // Workspace @-mention still chips...
    expect(screen.getByText("@src/server.ts")).toBeInTheDocument();
    // ...the materialized upload does not.
    expect(screen.queryByText(/uploads\/image\.png/)).toBeNull();
  });
});
