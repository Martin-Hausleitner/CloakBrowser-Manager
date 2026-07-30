import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TaskOutput } from "../../lib/api";
import { AgentOutputTimeline } from "./AgentOutputTimeline";

const output = (overrides: Partial<TaskOutput>): TaskOutput => ({
  id: "output-1",
  run_id: "run-1",
  sequence: 1,
  idempotency_key: "output-1",
  kind: "status",
  summary: "Working",
  payload: {},
  created_at: "2026-07-26T00:00:00Z",
  artifact_expired: false,
  ...overrides,
});

describe("AgentOutputTimeline", () => {
  it("renders typed actions screenshots data and errors without flattening to markdown", () => {
    render(
      <AgentOutputTimeline
        outputs={[
          output({
            kind: "action",
            summary: "Opened example.com",
            payload: { name: "navigate", url: "https://example.com" },
          }),
          output({
            id: "shot-1",
            sequence: 2,
            kind: "screenshot",
            summary: "Final screenshot",
          }),
          output({
            id: "data-1",
            sequence: 3,
            kind: "extracted_data",
            summary: "Page facts",
            payload: { data: { title: "Example Domain", ready: true } },
          }),
          output({
            id: "error-1",
            sequence: 4,
            kind: "error",
            summary: "Model timeout",
            payload: { code: "model_timeout", retryable: true },
          }),
        ]}
      />,
    );

    expect(screen.getByRole("list", { name: /agent output/i })).toBeTruthy();
    expect(screen.getByText("navigate")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Final screenshot" }).getAttribute("src")).toBe(
      "/api/task-outputs/shot-1/screenshot",
    );
    expect(screen.getByRole("table", { name: /Page facts/i })).toBeTruthy();
    expect(screen.getByText("Example Domain")).toBeTruthy();
    expect(screen.getByText("model_timeout")).toBeTruthy();
  });

  it("renders unknown future output kinds through a safe expandable fallback", () => {
    render(
      <AgentOutputTimeline
        outputs={[
          output({
            kind: "future_kind" as TaskOutput["kind"],
            summary: "Future output",
            payload: { safe: "value" },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Future output")).toBeTruthy();
    expect(screen.getByText(/details/i)).toBeTruthy();
    expect(screen.queryByText("<script>")).toBeNull();
  });

  it("groups repeated low-value events until the operator asks to show every event", () => {
    const { container } = render(
      <AgentOutputTimeline
        outputs={[
          output({ id: "metric-1", sequence: 1, kind: "metric", summary: "usage", payload: { name: "usage", value: 100 } }),
          output({ id: "metric-2", sequence: 2, kind: "metric", summary: "usage", payload: { name: "usage", value: 120 } }),
          output({ id: "action-1", sequence: 3, kind: "action", summary: "Navigate", payload: { name: "navigate" } }),
          output({ id: "metric-3", sequence: 4, kind: "metric", summary: "usage", payload: { name: "usage", value: 140 } }),
          output({ id: "summary-1", sequence: 5, kind: "summary", summary: "Completed", payload: { text: "Done" } }),
        ]}
      />,
    );

    expect(container.querySelectorAll('[data-output-kind="metric"]')).toHaveLength(2);
    expect(screen.getByText("2 events")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show all events" }));
    expect(container.querySelectorAll('[data-output-kind="metric"]')).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Group repeated events" })).toBeTruthy();
  });
});
