import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";

vi.mock("../lib/api", () => ({
  api: {
    login: vi.fn(),
  },
}));

describe("LoginPage", () => {
  it("keeps every account-login control at the shared mobile touch-target height", () => {
    render(<LoginPage accessControlEnabled onSuccess={vi.fn()} />);

    expect(screen.getByLabelText("Username").className).toContain("min-h-11");
    expect(screen.getByLabelText("Password").className).toContain("min-h-11");
    expect(screen.getByRole("button", { name: "Sign in" }).className).toContain("min-h-11");
    expect(screen.getByRole("button", { name: "Use an administrator token" }).className).toContain("min-h-11");
  });

  it("keeps token mode touch-friendly after switching authentication modes", () => {
    render(<LoginPage accessControlEnabled onSuccess={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Use an administrator token" }));

    expect(screen.getByPlaceholderText("Access token").className).toContain("min-h-11");
    expect(screen.getByRole("button", { name: "Unlock" }).className).toContain("min-h-11");
  });
});
