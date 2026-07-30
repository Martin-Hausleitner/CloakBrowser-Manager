import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Eye,
  Gauge,
  Image as ImageIcon,
  Link2,
  ListTree,
  MousePointer2,
  ShieldQuestion,
  Table2,
} from "lucide-react";
import { api, type TaskOutput, type TaskOutputKind } from "../../lib/api";

export interface AgentOutputTimelineProps {
  outputs: TaskOutput[];
}

const ICONS: Record<TaskOutputKind, typeof Activity> = {
  status: Activity,
  action: MousePointer2,
  observation: Eye,
  screenshot: ImageIcon,
  extracted_data: Table2,
  link: Link2,
  metric: Gauge,
  error: AlertTriangle,
  approval: ShieldQuestion,
  summary: CheckCircle2,
};

const GROUPABLE_KINDS = new Set<TaskOutputKind>(["metric", "observation", "status"]);

interface OutputGroup {
  output: TaskOutput;
  count: number;
}

function groupRepeatedOutputs(outputs: TaskOutput[]): OutputGroup[] {
  const grouped: OutputGroup[] = [];
  for (const output of outputs) {
    if (!GROUPABLE_KINDS.has(output.kind)) {
      grouped.push({ output, count: 1 });
      continue;
    }
    const key = `${output.kind}\u0000${output.summary}`;
    const previousIndex = grouped.length - 1;
    const previous = grouped[previousIndex];
    const previousKey = previous && GROUPABLE_KINDS.has(previous.output.kind)
      ? `${previous.output.kind}\u0000${previous.output.summary}`
      : null;
    if (previous && previousKey === key) {
      grouped[previousIndex] = { output, count: previous.count + 1 };
    } else {
      grouped.push({ output, count: 1 });
    }
  }
  return grouped;
}

function scalar(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function dataRows(payload: Record<string, unknown>): Array<[string, unknown]> {
  const value = payload.data ?? payload.fields;
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>);
}

function OutputBody({ output }: { output: TaskOutput }) {
  const payload = output.payload ?? {};

  if (output.kind === "screenshot") {
    if (output.artifact_expired) {
      return <p className="text-[11px] text-[#8b8b8b]">Screenshot expired</p>;
    }
    return (
      <img
        src={api.taskOutputScreenshotUrl(output.id)}
        alt={output.summary}
        className="mt-2 max-h-64 w-full rounded border border-[#303030] object-contain bg-black"
        loading="lazy"
      />
    );
  }

  if (output.kind === "extracted_data") {
    const rows = dataRows(payload);
    if (rows.length) {
      return (
        <table
          className="mt-2 w-full table-fixed text-left text-[11px]"
          aria-label={output.summary}
        >
          <tbody>
            {rows.map(([key, value]) => (
              <tr key={key} className="border-t border-[#2a2a2a]">
                <th className="w-1/3 py-1 pr-2 font-medium text-[#8b8b8b]">{key}</th>
                <td className="break-words py-1 text-[#d8d8d8]">{scalar(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    }
  }

  if (output.kind === "action") {
    return (
      <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
        {payload.name != null ? (
          <span className="rounded bg-[#26334a] px-1.5 py-0.5 text-[#b9d2ff]">
            {scalar(payload.name)}
          </span>
        ) : null}
        {payload.url != null ? (
          <span className="max-w-full truncate rounded bg-[#202020] px-1.5 py-0.5 text-[#a9a9a9]">
            {scalar(payload.url)}
          </span>
        ) : null}
        {payload.step != null ? (
          <span className="rounded bg-[#202020] px-1.5 py-0.5 text-[#a9a9a9]">
            step {scalar(payload.step)}
          </span>
        ) : null}
      </div>
    );
  }

  if (output.kind === "link" && typeof payload.url === "string") {
    return (
      <a
        className="mt-2 block truncate text-[11px] text-[#8bb8ff] underline decoration-[#35598f]"
        href={payload.url}
        target="_blank"
        rel="noreferrer"
      >
        {typeof payload.title === "string" ? payload.title : payload.url}
      </a>
    );
  }

  if (output.kind === "metric") {
    return (
      <p className="mt-2 text-[11px] text-[#cfcfcf]">
        {scalar(payload.name)}: <strong>{scalar(payload.value)}</strong>{" "}
        {payload.unit == null ? "" : scalar(payload.unit)}
      </p>
    );
  }

  if (output.kind === "error") {
    return (
      <p className="mt-2 text-[11px] text-red-300">
        <span>{scalar(payload.code)}</span>
        {payload.retryable === true ? <span> · retryable</span> : null}
      </p>
    );
  }

  if (output.kind === "observation" || output.kind === "summary" || output.kind === "status") {
    const text = payload.text ?? payload.result ?? payload.detail ?? payload.status;
    return text == null ? null : (
      <p className="mt-2 whitespace-pre-wrap text-[11px] text-[#cfcfcf]">{scalar(text)}</p>
    );
  }

  return (
    <details className="mt-2 text-[11px] text-[#a8a8a8]">
      <summary className="cursor-pointer select-none">Details</summary>
      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-[#0a0a0a] p-2">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </details>
  );
}

export function AgentOutputTimeline({ outputs }: AgentOutputTimelineProps) {
  const [showAll, setShowAll] = useState(false);
  const ordered = useMemo(() => [...outputs].sort((a, b) => a.sequence - b.sequence), [outputs]);
  const compact = useMemo(() => groupRepeatedOutputs(ordered), [ordered]);
  const visible = showAll ? ordered.map((output) => ({ output, count: 1 })) : compact;
  const hasGroupedEvents = compact.length < ordered.length;

  return (
    <div>
      {hasGroupedEvents ? (
        <div className="mb-1 flex justify-end">
          <button
            type="button"
            className="h-6 rounded border border-[#3c3c43] bg-[#18181b] px-2 text-[9px] font-medium text-[#d4d4d8] hover:bg-[#27272a]"
            onClick={() => setShowAll((current) => !current)}
          >
            {showAll ? "Group repeated events" : "Show all events"}
          </button>
        </div>
      ) : null}
      <ol className="space-y-1" aria-label="Agent output">
      {visible.map(({ output, count }) => {
        const Icon = ICONS[output.kind] ?? ListTree;
        const defaultOpen = !GROUPABLE_KINDS.has(output.kind);
        return (
          <li
            key={`${output.id}-${showAll ? "raw" : "grouped"}`}
            className="rounded border border-[#303036] bg-[#141417]"
            data-output-kind={output.kind}
          >
            <details className="group" open={defaultOpen || undefined}>
              <summary className="flex min-h-7 cursor-pointer list-none items-center gap-1.5 px-2 py-1 marker:hidden hover:bg-[#1c1c20]">
                <ChevronRight className="h-2.5 w-2.5 shrink-0 text-[#8f8f99] transition-transform group-open:rotate-90" />
                <Icon className="h-3 w-3 shrink-0 text-[#b4b4bd]" />
                <span className="min-w-0 flex-1 truncate text-[10px] font-medium text-[#f4f4f5]">
                    {output.summary}
                </span>
                {count > 1 ? (
                  <span className="rounded bg-[#302e58] px-1.5 py-0.5 text-[8px] font-semibold text-[#dedcff]">
                    {count} events
                  </span>
                ) : null}
                <span className="text-[8px] uppercase tracking-wide text-[#a1a1aa]">
                  {output.kind.replace("_", " ")}
                </span>
              </summary>
              <div className="border-t border-[#29292e] px-2 pb-2 pt-0.5">
                <OutputBody output={output} />
              </div>
            </details>
          </li>
        );
      })}
      </ol>
    </div>
  );
}
