import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditPolicyInstanceDialog } from "./PolicyInstanceEditor";

afterEach(cleanup);

describe("EditPolicyInstanceDialog", () => {
  it("renders prompt parameters as a multiline editor and saves them", () => {
    const onSave = vi.fn();
    render(
      <EditPolicyInstanceDialog
        policy={{
          name: "dangerous_actions",
          handler: "omnigent.policies.dangerous_actions",
          factory_params: null,
        }}
        registryEntry={{
          handler: "omnigent.policies.dangerous_actions",
          kind: "factory",
          name: "Dangerous Actions",
          description: "",
          params_schema: {
            type: "object",
            properties: {
              classification_prompt: {
                type: "string",
                "x-ui-widget": "textarea",
                default: "Original prompt",
              },
            },
          },
          requires_llm: true,
        }}
        modelIds={[]}
        open
        onOpenChange={vi.fn()}
        onSave={onSave}
        isPending={false}
        error={null}
      />,
    );

    const prompt = screen.getByLabelText(/classification_prompt/);
    expect(prompt.tagName).toBe("TEXTAREA");
    expect(prompt).toHaveValue("Original prompt");

    fireEvent.change(prompt, { target: { value: "Updated prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({
      name: "dangerous_actions",
      factory_params: { classification_prompt: "Updated prompt" },
    });
  });
});
