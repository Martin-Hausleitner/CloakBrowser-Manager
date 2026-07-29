import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, GripVertical } from "lucide-react";
import type {
  BrowserToolId,
  BrowserToolSelection,
  ProviderId,
  ProviderReadiness,
  ProviderReadinessProvider,
  ProviderTransport,
} from "../../lib/api";

export const DEFAULT_BROWSER_TOOLS: BrowserToolSelection[] = [
  { id: "unbrowse", enabled: true },
  { id: "stagehand", enabled: true },
  { id: "browser-harness", enabled: true },
];

export interface ProviderRoutingState {
  providerId: ProviderId;
  transport: ProviderTransport;
  modelAlias: string;
  browserTools: BrowserToolSelection[];
  routingPolicy: string;
}

interface ProviderToolControlProps {
  state: ProviderRoutingState;
  readiness: ProviderReadiness | null;
  toolReadiness?: Partial<Record<BrowserToolId, { ready: boolean; reason: string | null; test?: string }>>;
  disabled?: boolean;
  compactId?: string;
  onChange: (next: ProviderRoutingState) => void;
}

const PROVIDER_LABELS: Record<ProviderId, string> = {
  antigravity: "Antigravity",
  codex: "Codex",
  claude: "Claude",
  cursor: "Cursor",
  grok: "Grok",
  opencode: "OpenCode",
};

const PROVIDER_CHOICES: ProviderId[] = ["codex", "claude", "cursor", "grok", "opencode", "antigravity"];

function providerDefaultTransport(id: ProviderId): ProviderTransport {
  return id === "antigravity" ? "cli" : "acp";
}

const TOOL_LABELS: Record<BrowserToolId, string> = {
  unbrowse: "Unbrowse",
  stagehand: "Stagehand",
  "browser-harness": "Browser Harness",
};

export function providerInfo(
  readiness: ProviderReadiness | null,
  id: ProviderId,
  transport: ProviderTransport,
): ProviderReadinessProvider | null {
  return readiness?.providers.find((provider) =>
    provider.provider === id && provider.transport === transport,
  ) ?? null;
}

function toolReadinessLabel(tool: { ready: boolean; reason: string | null; test?: string } | null): string {
  if (!tool) return "Not checked";
  if (tool.ready) return tool.test === "passed" ? "Ready · test passed" : `Ready · ${tool.test}`;
  return tool.reason || "Unavailable";
}

export function providerToolOrderIsCanonical(
  tools: BrowserToolSelection[],
): boolean {
  const canonical = DEFAULT_BROWSER_TOOLS.map((tool) => tool.id);
  return tools.map((tool) => tool.id).join("|") === canonical.join("|");
}

export function providerLaunchBlockReason(
  state: ProviderRoutingState,
  readiness: ProviderReadiness | null,
): string | null {
  if (!readiness) return "Provider readiness is still loading.";
  const provider = providerInfo(readiness, state.providerId, state.transport);
  if (provider && !provider.ready) return provider.reason_code || `${PROVIDER_LABELS[state.providerId]} is unavailable`;
  if (!providerToolOrderIsCanonical(state.browserTools)) {
    return "Browser tool order is not executable until backend ordering support lands.";
  }
  return null;
}

export function ProviderToolControl({
  state,
  readiness,
  toolReadiness,
  disabled = false,
  compactId = "normal",
  onChange,
}: ProviderToolControlProps) {
  const [providerOpen, setProviderOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const providerButtonRef = useRef<HTMLButtonElement | null>(null);
  const toolsButtonRef = useRef<HTMLButtonElement | null>(null);
  const provider = providerInfo(readiness, state.providerId, state.transport);
  const activeTools = state.browserTools
    .filter((tool) => tool.enabled)
    .map((tool) => TOOL_LABELS[tool.id]);
  const modelOptions = provider?.model_aliases ?? [];
  const selectedModelAlias = modelOptions.includes(state.modelAlias) ? state.modelAlias : "";
  const sectionSuffix = compactId === "full" ? "-full" : "";

  useEffect(() => {
    if (!providerOpen && !toolsOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (toolsOpen) {
        setToolsOpen(false);
        window.setTimeout(() => toolsButtonRef.current?.focus({ preventScroll: true }), 0);
        return;
      }
      setProviderOpen(false);
      window.setTimeout(() => providerButtonRef.current?.focus({ preventScroll: true }), 0);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [providerOpen, toolsOpen]);

  const setProvider = (id: ProviderId) => {
    const nextTransport = providerDefaultTransport(id);
    const nextProvider = providerInfo(readiness, id, nextTransport);
    const nextModel = nextProvider?.model_aliases?.[0] ?? "";
    onChange({ ...state, providerId: id, transport: nextTransport, modelAlias: nextModel });
  };

  const moveTool = (id: BrowserToolId, direction: -1 | 1) => {
    const index = state.browserTools.findIndex((tool) => tool.id === id);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= state.browserTools.length) return;
    const next = [...state.browserTools];
    const [item] = next.splice(index, 1);
    if (!item) return;
    next.splice(nextIndex, 0, item);
    onChange({ ...state, browserTools: next });
  };

  const toggleTool = (id: BrowserToolId) => {
    onChange({
      ...state,
      browserTools: state.browserTools.map((tool) =>
        tool.id === id ? { ...tool, enabled: !tool.enabled } : tool,
      ),
    });
  };

  const blockReason = useMemo(() => providerLaunchBlockReason(state, readiness), [readiness, state]);

  return (
    <div className="min-w-0 text-[10px]" data-testid="provider-tool-control">
      <div
        className="flex min-w-0 items-center gap-1 rounded border border-[#33343a] bg-[#121216] px-1.5 py-1 text-[#d8d8df]"
        data-testid="provider-tool-summary"
        title={blockReason ?? undefined}
      >
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${blockReason ? "bg-amber-400" : "bg-emerald-400"}`} />
        <span className="min-w-0 flex-1 truncate">
          {PROVIDER_LABELS[state.providerId]} · {state.transport} · {selectedModelAlias || "default"} · {activeTools.join(" > ") || "No tools"}
        </span>
      </div>
      {blockReason ? (
        <div className="mt-1 rounded border border-amber-900/50 bg-amber-950/30 px-1.5 py-1 text-amber-200">
          {blockReason}
        </div>
      ) : null}
      <div className="mt-1 grid grid-cols-2 gap-1">
        <button
          ref={providerButtonRef}
          type="button"
          className="inline-flex h-7 items-center justify-center gap-1 rounded border border-[#363640] px-1.5 text-[#d4d4d8] hover:bg-[#222229]"
          onClick={() => setProviderOpen((open) => !open)}
          aria-label="Toggle KI Provider settings"
          aria-expanded={providerOpen}
          disabled={disabled}
        >
          KI Provider
          <ChevronDown className={`h-3 w-3 ${providerOpen ? "rotate-180" : ""}`} />
        </button>
        <button
          ref={toolsButtonRef}
          type="button"
          className="inline-flex h-7 items-center justify-center gap-1 rounded border border-[#363640] px-1.5 text-[#d4d4d8] hover:bg-[#222229]"
          onClick={() => setToolsOpen((open) => !open)}
          aria-label="Toggle Browser Tools settings"
          aria-expanded={toolsOpen}
          disabled={disabled}
        >
          Browser Tools
          <ChevronDown className={`h-3 w-3 ${toolsOpen ? "rotate-180" : ""}`} />
        </button>
      </div>
      {providerOpen ? (
        <div
          className="mt-1 rounded border border-[#33343a] bg-[#101014] p-1.5"
          data-testid={`provider-tool-provider-section${sectionSuffix}`}
        >
          <div className="grid grid-cols-2 gap-1" role="radiogroup" aria-label="Provider">
            {PROVIDER_CHOICES.map((id) => {
              const transport = providerDefaultTransport(id);
              const info = providerInfo(readiness, id, transport);
              return (
                <label key={id} className="flex min-w-0 items-center gap-1 rounded bg-[#17171b] px-1.5 py-1">
                  <input
                    type="radio"
                    name={`provider-${compactId}`}
                    checked={state.providerId === id}
                    onChange={() => setProvider(id)}
                    disabled={disabled || id === "antigravity" || Boolean(info && !info.ready)}
                    aria-label={PROVIDER_LABELS[id]}
                  />
                  <span className="min-w-0 flex-1 truncate">{PROVIDER_LABELS[id]}</span>
                  <span className={info?.ready ? "text-emerald-300" : "text-amber-300"}>
                    {info?.ready ? "Ready" : "Off"}
                  </span>
                </label>
              );
            })}
          </div>
          <label className="mt-1 block">
            <span className="sr-only">Model alias</span>
            <select
              className="input h-7 w-full bg-[#18181b] py-0.5 text-[10px]"
              value={state.modelAlias}
              onChange={(event) => onChange({ ...state, modelAlias: event.target.value })}
              aria-label="Model alias"
              disabled={disabled}
            >
              <option value="">default</option>
              {modelOptions.map((model) => <option key={model} value={model}>{model}</option>)}
            </select>
          </label>
          <label className="mt-1 block">
            <span className="sr-only">Routing policy</span>
            <select
              className="input h-7 w-full bg-[#18181b] py-0.5 text-[10px]"
              value={state.routingPolicy}
              onChange={(event) => onChange({ ...state, routingPolicy: event.target.value })}
              aria-label="Routing policy"
              disabled={disabled}
            >
              <option value="ordered-fallback">ordered-fallback</option>
            </select>
          </label>
          {readiness?.providers.map((item) => !item.ready && item.reason_code ? (
            <div key={`${item.provider}:${item.transport}`} className="mt-1 truncate text-amber-300">{item.reason_code}</div>
          ) : null)}
        </div>
      ) : null}
      {toolsOpen ? (
        <div
          className="mt-1 space-y-1 rounded border border-[#33343a] bg-[#101014] p-1.5"
          data-testid={`provider-tool-tools-section${sectionSuffix}`}
        >
          {state.browserTools.map((tool, index) => {
            const info = toolReadiness?.[tool.id] ?? null;
            const label = TOOL_LABELS[tool.id];
            return (
              <div key={tool.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-1 rounded bg-[#17171b] px-1.5 py-1">
                <label className="flex min-w-0 items-center gap-1">
                  <input
                    type="checkbox"
                    checked={tool.enabled}
                    disabled={disabled}
                    onChange={() => toggleTool(tool.id)}
                    aria-label={`Enable ${label}`}
                  />
                  <GripVertical className="h-3 w-3 shrink-0 text-[#777]" />
                  <span className="min-w-0 flex-1 truncate">{label}</span>
                </label>
                <span className={info?.ready ? "text-emerald-300" : "text-amber-300"}>
                  {toolReadinessLabel(info)}
                </span>
                <span className="flex gap-0.5">
                  <button
                    type="button"
                    className="h-6 w-6 rounded border border-[#3a3a42] text-[#c8c8cf] disabled:opacity-40"
                    onClick={() => moveTool(tool.id, -1)}
                    disabled={disabled || index === 0}
                    aria-label={`Move ${label} up`}
                  >
                    <ChevronUp className="mx-auto h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    className="h-6 w-6 rounded border border-[#3a3a42] text-[#c8c8cf] disabled:opacity-40"
                    onClick={() => moveTool(tool.id, 1)}
                    disabled={disabled || index === state.browserTools.length - 1}
                    aria-label={`Move ${label} down`}
                  >
                    <ChevronDown className="mx-auto h-3 w-3" />
                  </button>
                </span>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
