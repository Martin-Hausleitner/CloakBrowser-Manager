import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StreamingBenchmarkPanel } from "./StreamingBenchmarkPanel";

const apiMock = vi.hoisted(() => ({
  getBenchmarkReport: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  DEFAULT_BENCHMARK_REPORT_URL: "/benchmark-report.json",
  api: apiMock,
}));

describe("StreamingBenchmarkPanel", () => {
  beforeEach(() => {
    apiMock.getBenchmarkReport.mockReset();
  });

  it("renders nested runner milestones and labels unmeasured candidates honestly", async () => {
    apiMock.getBenchmarkReport.mockResolvedValue({
      started_at: "2026-07-20T09:55:00Z",
      finished_at: "2026-07-20T10:00:00Z",
      results: [
        {
          candidate: {
            id: "manager-vnc",
            name: "KasmVNC + noVNC VNC WebSocket handshake",
            type: "websocket",
            metadata: { version: "1.2.3" },
          },
          status: "measured",
          availability: "available",
          summary: {
            runs: 5,
            success_rate_pct: 100,
            timings_ms: { handshake_ms: { min: 10.4, median: 24.7, p95: 64.8, max: 64.8 } },
          },
        },
        {
          candidate: {
            id: "selkies",
            name: "Selkies browser-stream POC",
            type: "websocket",
          },
          status: "not_installed",
          availability: "not_measured",
          reason: "The required local dependency was not installed for this run.",
          summary: { runs: 0 },
        },
      ],
    });

    render(<StreamingBenchmarkPanel reportUrl="/custom-report.json" />);

    expect((await screen.findAllByText("KasmVNC + noVNC VNC WebSocket handshake")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Selkies browser-stream POC").length).toBeGreaterThan(0);
    expect(screen.getAllByText("24.7 ms").length).toBeGreaterThan(0);
    expect(screen.getAllByText("64.8 ms").length).toBeGreaterThan(0);
    expect(screen.getAllByText("100%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Handshake").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not Installed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("The required local dependency was not installed for this run.").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Download report" }).getAttribute("href")).toBe("/custom-report.json");
    expect(apiMock.getBenchmarkReport).toHaveBeenCalledWith("/custom-report.json");
  });

  it("shows an unavailable state and can retry the configured report URL", async () => {
    apiMock.getBenchmarkReport
      .mockRejectedValueOnce(new Error("not found"))
      .mockResolvedValueOnce({ candidates: [] });

    render(<StreamingBenchmarkPanel reportUrl="/missing.json" />);

    expect(await screen.findByText("Benchmark report unavailable")).toBeTruthy();
    expect(screen.getByText("Expected a machine-readable report at /missing.json.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Refresh benchmark report" }));

    await waitFor(() => expect(apiMock.getBenchmarkReport).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("No streaming benchmark candidates were reported yet.")).toBeTruthy();
  });
});
