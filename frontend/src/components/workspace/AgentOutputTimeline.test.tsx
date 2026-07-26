import { render, screen } from "@testing-library/react";
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
});
