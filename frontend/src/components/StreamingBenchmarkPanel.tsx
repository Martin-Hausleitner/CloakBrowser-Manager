import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Download, ExternalLink, RefreshCw, Timer } from "lucide-react";
import {
  DEFAULT_BENCHMARK_REPORT_URL,
  api,
  type BenchmarkCandidate,
  type BenchmarkMetricSet,
  type BenchmarkReport,
  type BenchmarkRunnerResult,
} from "../lib/api";

interface StreamingBenchmarkPanelProps {
  reportUrl?: string;
}

interface BenchmarkRow {
  key: string;
  name: string;
  version: string | null;
  technology: string | null;
  state: string;
  availability: string;
  measured: boolean;
  reason: string | null;
  milestone: string | null;
  medianMs: number | null;
  p95Ms: number | null;
  successRatePct: number | null;
  samples: number | null;
}

const measuredStates = new Set(["measured", "complete", "completed", "pass", "passed"]);
const activeStates = new Set(["queued", "running"]);
const unmeasuredStatusReason: Record<string, string> = {
  not_installed: "Technology was not installed in this benchmark run.",
  architecture_only: "Candidate was documented for architecture comparison only and was not measured.",
};

function titleCase(value: string | null | undefined) {
  if (!value) return "Unknown";
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function formatNumber(value: number | null | undefined, suffix = "") {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`
    : "Not measured";
}

function metricValue(metrics: BenchmarkMetricSet | null, keys: string[]) {
  if (!metrics) return null;
  for (const key of keys) {
    const value = metrics[key];
    if (typeof value === "number") return value;
  }
  return null;
}

function finiteNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isMeasured(candidate: BenchmarkCandidate) {
  if (candidate.measured === false) return false;
  if (candidate.measured === true) return true;
  if (candidate.state && measuredStates.has(candidate.state.toLowerCase())) return true;
  if (candidate.state === "not_measured") return false;
  return Boolean(candidate.metrics);
}

function metadataVersion(metadata: BenchmarkRunnerResult["candidate"]["metadata"]) {
  const version = metadata?.version ?? metadata?.candidate_version;
  return typeof version === "string" || typeof version === "number" ? String(version) : null;
}

const timingPriority = [
  "input_response_ms",
  "frame_ready_ms",
  "handshake_ms",
  "first_byte_ms",
  "total_ms",
  "ready_ms",
  "connect_ms",
  "exit_ms",
];

function timingLabel(key: string) {
  return titleCase(key.replace(/_ms$/, "").replace(/_/g, " "));
}

function runnerMilestone(result: BenchmarkRunnerResult) {
  const timings = result.summary?.timings_ms;
  if (!timings) return null;

  const entries = Object.entries(timings)
    .filter(([, rollup]) => finiteNumber(rollup?.median) !== null)
    .sort(([left], [right]) => {
      const leftPriority = timingPriority.indexOf(left);
      const rightPriority = timingPriority.indexOf(right);
      return (leftPriority === -1 ? timingPriority.length : leftPriority)
        - (rightPriority === -1 ? timingPriority.length : rightPriority);
    });
  const [key, rollup] = entries[0] ?? [];
  if (!key || !rollup) return null;
  return {
    label: timingLabel(key),
    medianMs: finiteNumber(rollup.median),
    p95Ms: finiteNumber(rollup.p95) ?? finiteNumber(rollup.max),
  };
}

function normalizeRows(report: BenchmarkReport | null): BenchmarkRow[] {
  if (!report) return [];

  const rows = new Map<string, BenchmarkRow>();

  for (const result of report.results ?? []) {
    const candidate = result.candidate;
    const name = candidate.name || candidate.id || candidate.type || "Unnamed candidate";
    const technology = typeof candidate.metadata?.technology === "string"
      ? candidate.metadata.technology
      : candidate.type ?? candidate.name ?? null;
    const version = metadataVersion(candidate.metadata);
    const key = `${candidate.id ?? technology ?? name}:${version ?? ""}`.toLowerCase();
    const measured = result.status === "measured";
    const milestone = runnerMilestone(result);
    rows.set(key, {
      key,
      name,
      version,
      technology,
      state: result.status,
      availability: result.availability ?? (measured ? "not_measured" : "not_measured"),
      measured,
      reason: result.reason ?? unmeasuredStatusReason[result.status] ?? null,
      milestone: milestone?.label ?? null,
      medianMs: milestone?.medianMs ?? null,
      p95Ms: milestone?.p95Ms ?? null,
      successRatePct: finiteNumber(result.summary?.success_rate_pct),
      samples: finiteNumber(result.summary?.runs),
    });
  }

  for (const candidate of report.candidates ?? []) {
    const name = candidate.name || candidate.technology || "Unnamed candidate";
    const technology = candidate.technology ?? candidate.name ?? null;
    const key = `${technology ?? name}:${candidate.version ?? ""}`.toLowerCase();
    const measured = isMeasured(candidate);
    rows.set(key, {
      key,
      name,
      version: candidate.version ?? null,
      technology,
      state: candidate.state ?? (measured ? "measured" : "not_measured"),
      availability: measured ? "available" : "not_measured",
      measured,
      reason: candidate.not_measured_reason ?? candidate.missing_reason ?? null,
      milestone: measured ? "Reported latency" : null,
      medianMs: measured ? metricValue(candidate.metrics ?? null, ["p50_latency_ms", "median_latency_ms", "avg_latency_ms"]) : null,
      p95Ms: measured ? metricValue(candidate.metrics ?? null, ["p95_latency_ms"]) : null,
      successRatePct: measured ? metricValue(candidate.metrics ?? null, ["availability_pct", "success_rate_pct"]) : null,
      samples: measured ? metricValue(candidate.metrics ?? null, ["samples"]) : null,
    });
  }

  for (const expected of report.expected_technologies ?? []) {
    const name = typeof expected === "string" ? expected : expected.name;
    const version = typeof expected === "string" ? null : expected.version ?? null;
    const key = `${name}:${version ?? ""}`.toLowerCase();
    const hasReportedTechnology = [...rows.values()].some((row) => {
      if (version && row.version !== version) return false;
      return (row.technology ?? row.name).toLowerCase() === name.toLowerCase();
    });
    if (!hasReportedTechnology && !rows.has(key)) {
      rows.set(key, {
        key,
        name,
        version,
        technology: name,
        state: "not_measured",
        availability: "not_measured",
        measured: false,
        reason: "Technology was listed in the benchmark plan but no measurement was reported.",
        milestone: null,
        medianMs: null,
        p95Ms: null,
        successRatePct: null,
        samples: null,
      });
    }
  }

  return [...rows.values()].sort((left, right) => left.name.localeCompare(right.name));
}

function runTimestamp(report: BenchmarkReport | null) {
  return report?.run?.generated_at
    ?? report?.generated_at
    ?? report?.finished_at
    ?? report?.run?.finished_at
    ?? report?.started_at
    ?? report?.run?.started_at
    ?? null;
}

function rowStatus(row: BenchmarkRow) {
  const resultWasAvailable = row.measured && row.availability === "available";
  return {
    label: row.measured ? "Measured" : titleCase(row.state),
    resultWasAvailable,
    className: resultWasAvailable
      ? "bg-emerald-500/15 text-emerald-300"
      : row.measured
        ? "bg-amber-500/15 text-amber-300"
        : activeStates.has(row.state.toLowerCase())
          ? "bg-sky-500/15 text-sky-300"
          : "bg-gray-500/15 text-gray-300",
  };
}

export function StreamingBenchmarkPanel({
  reportUrl = DEFAULT_BENCHMARK_REPORT_URL,
}: StreamingBenchmarkPanelProps) {
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const rows = useMemo(() => normalizeRows(report), [report]);
  const effectiveReportUrl = reportUrl;
  const runState = report?.run?.state ?? (report ? "complete" : "unknown");
  const lastUpdated = runTimestamp(report);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const nextReport = await api.getBenchmarkReport(reportUrl);
      setReport(nextReport);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load benchmark report");
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, [reportUrl]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section className="min-h-full bg-surface-0 px-6 py-6 text-gray-100" aria-labelledby="streaming-benchmark-heading">
      <div className="mx-auto flex max-w-6xl flex-col gap-5">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-accent">Cloak streaming lab</p>
            <h1 id="streaming-benchmark-heading" className="mt-1 text-2xl font-semibold">
              Live streaming benchmark results
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-400">
              Runtime report for browser streaming candidates. Missing entries are labeled as not measured and are not treated as performance results.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={effectiveReportUrl}
              className="btn-secondary inline-flex min-h-9 items-center gap-1.5"
              download
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              Download report
            </a>
            <a
              href={effectiveReportUrl}
              className="btn-secondary inline-flex min-h-9 items-center gap-1.5"
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink className="h-4 w-4" aria-hidden="true" />
              Open JSON
            </a>
            <button
              type="button"
              onClick={() => void refresh()}
              className="btn-primary inline-flex min-h-9 items-center gap-1.5"
              aria-label="Refresh benchmark report"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
              Refresh
            </button>
          </div>
        </header>

        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-md border border-border bg-surface-1 p-4">
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <Activity className="h-4 w-4 text-accent" aria-hidden="true" />
              Run state
            </div>
            <p className="mt-2 text-lg font-semibold">{titleCase(runState)}</p>
          </div>
          <div className="rounded-md border border-border bg-surface-1 p-4">
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <Timer className="h-4 w-4 text-accent" aria-hidden="true" />
              Last update
            </div>
            <p className="mt-2 text-lg font-semibold">{lastUpdated ? new Date(lastUpdated).toLocaleString() : "Not reported"}</p>
          </div>
          <div className="rounded-md border border-border bg-surface-1 p-4">
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <AlertTriangle className="h-4 w-4 text-accent" aria-hidden="true" />
              Coverage
            </div>
            <p className="mt-2 text-lg font-semibold">
              {rows.filter((row) => row.measured).length} measured / {rows.length || 0} listed
            </p>
          </div>
        </div>

        {loading && (
          <div className="rounded-md border border-border bg-surface-1 p-6 text-sm text-gray-400" role="status">
            Loading benchmark report...
          </div>
        )}

        {!loading && error && (
          <div className="rounded-md border border-red-600/30 bg-red-600/15 p-4 text-sm text-red-300" role="alert">
            <p className="font-medium">Benchmark report unavailable</p>
            <p className="mt-1 text-red-200/80">{error}</p>
            <p className="mt-2 text-red-200/70">Expected a machine-readable report at {reportUrl}.</p>
          </div>
        )}

        {!loading && !error && rows.length === 0 && (
          <div className="rounded-md border border-border bg-surface-1 p-6 text-sm text-gray-400">
            No streaming benchmark candidates were reported yet.
          </div>
        )}

        {!loading && !error && rows.length > 0 && (
          <>
            <div className="grid gap-3 md:hidden">
              {rows.map((row) => {
                const status = rowStatus(row);
                return (
                  <article key={row.key} className="rounded-md border border-border bg-surface-1 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h2 className="text-sm font-semibold text-gray-100">{row.name}</h2>
                        <p className="mt-1 text-xs text-gray-500">{row.technology ?? "Technology not reported"}</p>
                      </div>
                      <span className={`shrink-0 rounded-md px-2 py-1 text-xs font-medium ${status.className}`}>
                        {status.label}
                      </span>
                    </div>
                    {(!row.measured || !status.resultWasAvailable) && (
                      <p className="mt-3 text-xs leading-5 text-gray-500">
                        {row.reason ?? "No performance claim is shown for this entry."}
                      </p>
                    )}
                    <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-border pt-3 text-sm">
                      <div>
                        <dt className="text-xs text-gray-500">Median milestone</dt>
                        <dd className="mt-1 font-medium text-gray-200">{formatNumber(row.measured ? row.medianMs : null, " ms")}</dd>
                        {row.milestone ? <p className="mt-1 text-xs text-gray-500">{row.milestone}</p> : null}
                      </div>
                      <div>
                        <dt className="text-xs text-gray-500">P95 milestone</dt>
                        <dd className="mt-1 font-medium text-gray-200">{formatNumber(row.measured ? row.p95Ms : null, " ms")}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-gray-500">Success rate</dt>
                        <dd className="mt-1 font-medium text-gray-200">{formatNumber(row.measured ? row.successRatePct : null, "%")}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-gray-500">Samples</dt>
                        <dd className="mt-1 font-medium text-gray-200">{formatNumber(row.measured ? row.samples : null)}</dd>
                      </div>
                    </dl>
                  </article>
                );
              })}
            </div>
            <div className="hidden overflow-hidden rounded-md border border-border bg-surface-1 md:block">
              <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="border-b border-border bg-surface-2 text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th scope="col" className="px-4 py-3">Candidate</th>
                    <th scope="col" className="px-4 py-3">Technology</th>
                    <th scope="col" className="px-4 py-3">Measurement</th>
                    <th scope="col" className="px-4 py-3">Median milestone</th>
                    <th scope="col" className="px-4 py-3">P95 milestone</th>
                    <th scope="col" className="px-4 py-3">Success rate</th>
                    <th scope="col" className="px-4 py-3">Samples</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rows.map((row) => {
                    const status = rowStatus(row);
                    return (
                      <tr key={row.key} className="align-top">
                        <td className="px-4 py-4">
                          <div className="font-medium text-gray-100">{row.name}</div>
                          <div className="mt-1 text-xs text-gray-500">{row.version ? `v${row.version}` : "Version not reported"}</div>
                        </td>
                        <td className="px-4 py-4 text-gray-300">{row.technology ?? "Not reported"}</td>
                        <td className="px-4 py-4">
                          <span className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${
                            status.className
                          }`}
                          >
                            {status.label}
                          </span>
                          {(!row.measured || !status.resultWasAvailable) && (
                            <p className="mt-2 max-w-56 text-xs leading-5 text-gray-500">
                              {row.reason ?? "Not measured in this report. No performance claim is shown."}
                            </p>
                          )}
                        </td>
                        <td className="px-4 py-4 text-gray-300">
                          <div>{formatNumber(row.measured ? row.medianMs : null, " ms")}</div>
                          {row.milestone ? <div className="mt-1 text-xs text-gray-500">{row.milestone}</div> : null}
                        </td>
                        <td className="px-4 py-4 text-gray-300">{formatNumber(row.measured ? row.p95Ms : null, " ms")}</td>
                        <td className="px-4 py-4 text-gray-300">{formatNumber(row.measured ? row.successRatePct : null, "%")}</td>
                        <td className="px-4 py-4 text-gray-300">{formatNumber(row.measured ? row.samples : null)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              </div>
            </div>
          </>
        )}

        {report?.notes && (
          <div className="rounded-md border border-border bg-surface-1 p-4 text-sm leading-6 text-gray-400">
            {report.notes}
          </div>
        )}
      </div>
    </section>
  );
}
