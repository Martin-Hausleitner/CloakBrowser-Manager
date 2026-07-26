import {
  Activity,
  AlertTriangle,
  CheckCircle2,
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
  const ordered = [...outputs].sort((a, b) => a.sequence - b.sequence);

  return (
    <ol className="space-y-2" aria-label="Agent output">
      {ordered.map((output) => {
        const Icon = ICONS[output.kind] ?? ListTree;
        return (
          <li
            key={output.id}
            className="rounded border border-[#2a2a2a] bg-[#151515] p-2.5"
            data-output-kind={output.kind}
          >
            <div className="flex items-start gap-2">
              <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#8b8b8b]" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[11px] font-medium text-[#e4e4e4]">
                    {output.summary}
                  </span>
                  <span className="ml-auto text-[9px] uppercase tracking-wide text-[#666]">
                    {output.kind.replace("_", " ")}
                  </span>
                </div>
                <OutputBody output={output} />
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
