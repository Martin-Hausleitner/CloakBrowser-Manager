import { useEffect, useMemo, useState } from "react";
import {
  api,
  type ExtensionDefaultItem,
  type ProfileCreateData,
  type ProfileHarness,
  type ProfileTemplate,
} from "../lib/api";
import { HARNESS_OPTIONS } from "../lib/harnessOptions";
import { ProfileForm } from "./ProfileForm";

interface CreateProfileFlowProps {
  projectId: string;
  harness: ProfileHarness;
  onCreated: (profileId: string) => Promise<void> | void;
  onSave: (data: ProfileCreateData) => Promise<void>;
  onCancel: () => void;
}

type Mode = "generate" | "template" | "existing";

export function CreateProfileFlow({
  projectId,
  harness,
  onCreated,
  onSave,
  onCancel,
}: CreateProfileFlowProps) {
  const [mode, setMode] = useState<Mode>("generate");
  const [templates, setTemplates] = useState<ProfileTemplate[]>([]);
  const [defaults, setDefaults] = useState<ExtensionDefaultItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [templateId, setTemplateId] = useState("generate-new");
  const [name, setName] = useState("");
  const [quickHarness, setQuickHarness] = useState<ProfileHarness>(harness);
  const [geoip, setGeoip] = useState(true);
  const [humanize, setHumanize] = useState(true);
  const [showFullForm, setShowFullForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [systemPrompt, setSystemPrompt] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const [templateRows, defaultsPayload] = await Promise.all([
          api.listProfileTemplates(),
          api.getExtensionDefaults(),
        ]);
        setTemplates(templateRows);
        setDefaults(defaultsPayload.extensions || defaultsPayload.items || []);
        setSelectedIds(defaultsPayload.selected_ids || []);
        const first = templateRows.find((row) => row.id === "generate-new") || templateRows[0];
        if (first) {
          setTemplateId(first.id);
          setSystemPrompt(first.system_prompt || "");
          setName(await suggestName(first.id, projectId));
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load templates");
      }
    })();
  }, [projectId]);

  const selectedTemplate = useMemo(
    () => templates.find((row) => row.id === templateId) || null,
    [templateId, templates],
  );

  const persistDefaults = async (next: string[]) => {
    setSelectedIds(next);
    try {
      const updated = await api.updateExtensionDefaults(next);
      setDefaults(updated.extensions || updated.items || []);
      setSelectedIds(updated.selected_ids || next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save extension defaults");
    }
  };

  const createFromTemplate = async () => {
    setBusy(true);
    setError(null);
    try {
      const profile = await api.createProfileFromTemplate(templateId, {
        name: name.trim() || undefined,
        project_id: projectId,
        harness: quickHarness,
        apply_default_extensions: true,
      });
      await onCreated(profile.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setBusy(false);
    }
  };

  if (showFullForm || mode === "existing") {
    return (
      <ProfileForm
        profile={null}
        onSave={onSave}
        onCancel={onCancel}
      />
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-100">Create profile</h2>
          <p className="mt-0.5 text-xs text-gray-500">
            Generate New or pick a template · defaults are API-backed
          </p>
        </div>
        <button type="button" className="btn btn-secondary text-xs" onClick={onCancel}>
          Cancel
        </button>
      </div>

      {error ? (
        <div className="rounded border border-red-600/30 bg-red-600/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-3 gap-2">
        {(
          [
            ["generate", "Generate New"],
            ["template", "Select template"],
            ["existing", "Full form"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={`rounded-lg border px-2 py-2 text-xs ${
              mode === value ? "border-accent/50 bg-accent/10 text-gray-100" : "border-border text-gray-400"
            }`}
            onClick={() => {
              setMode(value);
              if (value === "existing") setShowFullForm(true);
              if (value === "generate") setTemplateId("generate-new");
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === "template" ? (
        <label className="block text-xs text-gray-400">
          Template
          <select
            className="input mt-1 text-xs"
            value={templateId}
            onChange={async (event) => {
              const nextId = event.target.value;
              setTemplateId(nextId);
              const match = templates.find((row) => row.id === nextId);
              setSystemPrompt(match?.system_prompt || "");
              setName(await suggestName(nextId, projectId));
              if (match?.harness) setQuickHarness(match.harness);
            }}
          >
            {templates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <div className="grid gap-3 rounded-xl border border-border bg-surface-1 p-3 sm:grid-cols-2">
        <label className="block text-xs text-gray-400 sm:col-span-2">
          Auto name
          <input
            className="input mt-1 text-xs"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="block text-xs text-gray-400">
          Harness
          <select
            className="input mt-1 text-xs"
            value={quickHarness}
            onChange={(event) => setQuickHarness(event.target.value as ProfileHarness)}
          >
            {HARNESS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end gap-3 pb-1 text-xs text-gray-400">
          <label className="inline-flex items-center gap-1.5">
            <input type="checkbox" checked={geoip} onChange={(e) => setGeoip(e.target.checked)} />
            GeoIP
          </label>
          <label className="inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={humanize}
              onChange={(e) => setHumanize(e.target.checked)}
            />
            Humanize
          </label>
        </div>
        {systemPrompt || selectedTemplate?.summary ? (
          <div className="sm:col-span-2 rounded-lg bg-surface-2 px-2.5 py-2 text-[11px] text-gray-400">
            <div className="mb-1 font-semibold uppercase tracking-wide text-gray-500">
              System prompt
            </div>
            {systemPrompt || selectedTemplate?.summary}
          </div>
        ) : null}
      </div>

      <div className="rounded-xl border border-border bg-surface-1 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <p className="text-xs font-medium text-gray-200">Default extensions (Comet)</p>
            <p className="text-[10px] text-gray-500">
              Saved via PUT /api/extension/defaults · applied on create
            </p>
          </div>
        </div>
        <div className="max-h-48 space-y-1 overflow-y-auto">
          {defaults.map((item) => (
            <label
              key={item.id}
              className="flex items-start gap-2 rounded px-1.5 py-1 text-xs hover:bg-surface-2"
            >
              <input
                type="checkbox"
                className="mt-0.5"
                checked={selectedIds.includes(item.id)}
                onChange={(event) => {
                  const next = event.target.checked
                    ? [...selectedIds, item.id]
                    : selectedIds.filter((id) => id !== item.id);
                  void persistDefaults(next);
                }}
              />
              <span className="min-w-0">
                <span className="text-gray-200">{item.name}</span>
                {item.description ? (
                  <span className="mt-0.5 block text-[10px] text-gray-500">{item.description}</span>
                ) : null}
                {!item.available ? (
                  <span className="mt-0.5 block text-[10px] text-amber-400/80">
                    Sync to EXTENSION_CATALOG_DIR to install on launch
                  </span>
                ) : null}
              </span>
            </label>
          ))}
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <button
          type="button"
          className="btn btn-secondary text-xs"
          onClick={() => setShowFullForm(true)}
        >
          All settings
        </button>
        <button
          type="button"
          className="btn btn-primary text-xs"
          disabled={busy}
          onClick={() => void createFromTemplate()}
        >
          {busy ? "Creating…" : "Create"}
        </button>
      </div>
    </div>
  );
}

async function suggestName(templateId: string, projectId: string) {
  const stamp = Math.random().toString(36).slice(2, 6).toUpperCase();
  return `${templateId} · ${projectId}-${stamp}`;
}
