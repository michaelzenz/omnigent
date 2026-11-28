import { describe, expect, it } from "vitest";
import { boardSessionId } from "./BoardMarkdown";

const page = {
  href: "https://example.test/globalhub",
  origin: "https://example.test",
  protocol: "https:",
  host: "example.test",
};

describe("boardSessionId", () => {
  it.each([
    ["a relative session path", "/c/s_1"],
    ["a session URL on this server", "https://example.test/c/s_1/"],
    ["a session deep link to this server", "omnigent://example.test/c/s_1"],
  ])("opens %s in the app", (_case, href) => {
    expect(boardSessionId(href, page)).toBe("s_1");
  });

  it.each([
    ["a session URL on another server", "https://other.example/c/s_1"],
    ["a session deep link to another server", "omnigent://other.example/c/s_1"],
    ["a path that is not a session", "/c/s_1/files"],
    ["a web link", "https://example.com/docs"],
  ])("keeps %s as a normal link", (_case, href) => {
    expect(boardSessionId(href, page)).toBeNull();
  });
});
