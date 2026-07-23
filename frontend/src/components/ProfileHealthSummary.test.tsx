import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type ProfileHealth } from "../lib/api";
import { ProfileHealthSummary } from "./ProfileHealthSummary";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getProfileHealth: vi.fn(),
      runProfileHealth: vi.fn(),
    },
  };
});

const warningHealth: ProfileHealth = {
  profile_id: "profile-1",
  state: "warning",
  checked_at: "2026-07-22T12:00:00+00:00",
  proxy_configured: true,
  proxy_reachable: true,
  outbound_ip_masked: "203.0.113.x",
  proxy_latency_ms: 24.5,
  proxy_risk_score: 12,
  proxy_authenticity_score: 88,
  fingerprint_consistency_score: 80,
  browser_scan_score: null,
  warnings: ["timezone_mismatch"],
  blockers: ["browser_scan_consent"],
  error_code: null,
  sources: {
    browser_network: "measured",
    proxy_authenticity: "derived",
    browser_scan: "unavailable",
    proxychecker: "skipped",
  },
};

beforeEach(() => {
  vi.mocked(api.getProfileHealth).mockReset();
  vi.mocked(api.runProfileHealth).mockReset();
  vi.mocked(api.getProfileHealth).mockResolvedValue(warningHealth);
});

describe("ProfileHealthSummary", () => {
  it("shows a compact state and explicit measured, derived, unavailable and skipped details", async () => {
    render(<ProfileHealthSummary profileId="profile-1" canRun={false} running={false} />);

    expect(await screen.findByText(/Health warning/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Health warning/));

    expect(screen.getByText("Profile health")).toBeTruthy();
    expect(screen.getByText("203.0.113.x")).toBeTruthy();
    expect(screen.getByText("88/100")).toBeTruthy();
    expect(screen.getByText("80/100")).toBeTruthy();
    expect(screen.getByText("Measured")).toBeTruthy();
    expect(screen.getByText("Derived")).toBeTruthy();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getByText("Skipped")).toBeTruthy();
    expect(screen.getByText("Timezone mismatch")).toBeTruthy();
    expect(screen.getByText("Browser scan consent")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run health check" })).toBeNull();
  });

  it("never renders unknown provider fields or raw errors", async () => {
    vi.mocked(api.getProfileHealth).mockResolvedValue({
      ...warningHealth,
      proxy: "http://secret-user:secret-password@proxy.example:8080",
      provider_response: "private provider payload",
      exception_message: "private exception",
    } as ProfileHealth);

    const { container } = render(
      <ProfileHealthSummary profileId="profile-1" canRun={false} running={false} />,
    );
    await screen.findByText(/Health warning/);
    fireEvent.click(screen.getByText(/Health warning/));

    expect(container.textContent).not.toContain("secret-password");
    expect(container.textContent).not.toContain("proxy.example");
    expect(container.textContent).not.toContain("private provider payload");
    expect(container.textContent).not.toContain("private exception");
  });

  it("offers rerun only to an operator of a running profile and refreshes the result", async () => {
    vi.mocked(api.runProfileHealth).mockResolvedValue({ ...warningHealth, state: "pending" });
    render(<ProfileHealthSummary profileId="profile-1" canRun running />);

    await screen.findByText(/Health warning/);
    fireEvent.click(screen.getByText(/Health warning/));
    fireEvent.click(screen.getByRole("button", { name: "Run health check" }));

    await waitFor(() => expect(api.runProfileHealth).toHaveBeenCalledWith("profile-1"));
    expect(await screen.findByText(/Health pending/)).toBeTruthy();
  });

  it("keeps rerun disabled while the profile is stopped", async () => {
    render(<ProfileHealthSummary profileId="profile-1" canRun running={false} />);

    await screen.findByText(/Health warning/);
    fireEvent.click(screen.getByText(/Health warning/));
    expect((screen.getByRole("button", { name: "Run health check" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
