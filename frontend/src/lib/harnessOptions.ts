import type { ProfileHarness } from "./api";

export type HarnessOption = {
  value: ProfileHarness;
  label: string;
  short: string;
  approach: "browser-first" | "api-first" | "production" | "agent-bridge";
  description: string;
};

/** Preferred harness is metadata only — host actions still require Codex Computer Use. */
export const HARNESS_OPTIONS: HarnessOption[] = [
  {
    value: "browser-use",
    label: "Browser Use",
    short: "BU",
    approach: "browser-first",
    description: "Excerpt-style agent shell · CDP browser control",
  },
  {
    value: "browser-harness",
    label: "Browser Harness",
    short: "BH",
    approach: "browser-first",
    description: "Thin self-healing CDP harness · max DOM freedom",
  },
  {
    value: "unbrowse",
    label: "Unbrowse",
    short: "UB",
    approach: "api-first",
    description: "API-native skills · sniff once, call fast",
  },
  {
    value: "stagehand",
    label: "Stagehand",
    short: "SH",
    approach: "production",
    description: "Act/Observe/Extract · production CDP primitives",
  },
  {
    value: "codex",
    label: "Codex",
    short: "CX",
    approach: "agent-bridge",
    description: "Codex Computer Use execution boundary",
  },
  {
    value: "antigravity",
    label: "Antigravity",
    short: "AG",
    approach: "agent-bridge",
    description: "Preferred agent metadata (non-executing)",
  },
  {
    value: "claude-code",
    label: "Claude Code",
    short: "CC",
    approach: "agent-bridge",
    description: "Preferred agent metadata (non-executing)",
  },
  {
    value: "opencode",
    label: "OpenCode",
    short: "OC",
    approach: "agent-bridge",
    description: "Preferred agent metadata (non-executing)",
  },
];

export const CALLABLE_BROWSER_HARNESSES: ProfileHarness[] = [
  "browser-use",
  "browser-harness",
  "unbrowse",
  "stagehand",
];

export function harnessLabel(value: ProfileHarness | string | undefined): string {
  return HARNESS_OPTIONS.find((option) => option.value === value)?.label ?? value ?? "—";
}
