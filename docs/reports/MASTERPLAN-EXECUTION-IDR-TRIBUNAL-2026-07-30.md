# Masterplan-Ausführung — finales IDR / Tribunal

| Feld | Wert |
| --- | --- |
| **Datum (UTC-Auditfenster)** | 2026-07-30 |
| **Tribunal-Rolle** | Integration Design Review + Freigabeentscheidung |
| **Quellplan** | Commit [`1e1617e`](https://github.com/Martin-Hausleitner/CloakBrowser-Manager/commit/1e1617e447f28887b6d2560614a5abb2e0ba52b8) · [`docs/superpowers/plans/2026-07-30-stable-browser-use-antigravity-masterplan.md`](../superpowers/plans/2026-07-30-stable-browser-use-antigravity-masterplan.md) |
| **Audit-Workspace** | `/home/coder/orca/workspaces/CloakBrowser-Manager-browser-use/cbm-acpx-agent-family` |
| **Branch (Worktree)** | `Martin-Hausleitner/cbm-acpx-agent-family` |
| **HEAD** | `c0d4f10139dab9d0eb4c76d73268375df88c98b3` (`c0d4f10`) |
| **Plan-„verified head“** | `827c0f087a8d2e1228f29f0c4225bb539d7795af` (`827c0f0`) |
| **Modus** | Evidenzgewichtet, read-only Synthese der Audit-Artefakte; **keine** Secrets, keine Live-Login-Mutation |
| **Primäre Audit-Artefakte** | `/tmp/cbm-plan-audit/01`, `03`–`13` (siehe §15) |

---

## 1. Executive Verdict — **HARD NO-GO**

**Entscheidung:** **HARD NO-GO** für eine volle Produktionsfreigabe des Stable-Browser-Use- + Antigravity-Masterplans auf dem geprüften HEAD und dem aktuell deployeden VCVM-Runtime.

### 1.1 Ein-Satz-Begründung

Der Masterplan ist als Spezifikation stark, aber der **kritische Produktpfad** (*Provider Antigravity → Harness Browser Use → ein Manager-Profil / ein Lease / ein Browser-Prozess*) ist **weder im API-Vertrag freigeschaltet noch als Adapter implementiert noch live E2E bewiesen**; die öffentliche VCVM-Route repräsentiert zudem einen **anderen, älteren Release-Stand**.

### 1.2 Fünf harte Fakten (auditverifiziert)

1. **Genau 66 Plan-Checkboxen** (Zeilenanfang, Tasks 1–11 + Definition of Done) bleiben **offen**, **0 checked**; die Prosa-Erwähnung der Checkbox-Syntax in der Plan-Kopfzeile zählt **nicht** als Tracking-Checkbox. In dieser Synthese wurde **keine** Checkbox abgehakt.
2. **`scripts/antigravity_cli_chat_model.py`** und **`e2e/tests/test_browser_use_antigravity_manager_e2e.py`** fehlen; `browser_use_worker` erlaubt nur `cursor-agent` / `claude-cli` / `grok-cli`.
3. **`TaskRunCreate`** lehnt den Routing-Vertrag ab, wenn `harness != "acpx"` — damit ist `provider=antigravity` + `harness=browser-use` **kontrahiert blockiert**.
4. Deployed: Health **OK**, aber **kein** Commit-Match zu `c0d4f10` / `827c0f0`; laufender Browser mit **`--disable-extensions`**; ACPX-Worker **stale/inactive**.
5. Evidenzgewichteter Feature-Index: **49,6 %** (26 Features, Summe 129/260) — siehe §3.

### 1.3 Was „NO-GO“ **nicht** bedeutet

- **Kein** generelles Urteil „Codebase wertlos“: Unit/Contract-Schichten (Leases, Vault Non-Reveal, ACPX-Pin, Tool-Router, Auth-Benchmark) sind **teilweise stark**.
- **Kein** Verbot weiterer Entwicklung: explizit **GO für gezielte Ticket-Arbeit** (§11), **NO-GO für Production/main-Merge-Claim** des Masterplan-Ziels.

---

## 2. Evidenzschichten (strikt trennen)

Jede Behauptung in diesem Report muss einer Schicht zugeordnet werden. **Höhere Schichten ersetzen niedrigere nur mit frischem Beleg am exakten Commit.**

| Schicht | Definition | Beispiel (dieser Audit) | Freigabetauglich? |
| --- | --- | --- | --- |
| **L0 Code/Static** | Dateien, Symbole, YAML, Manifeste am HEAD | Adapter-Dateien vorhanden/fehlend; CI-YAML parsed | Nein allein |
| **L1 Unit/Contract** | Deterministische Tests ohne echtes Manager-Profil-Lease | 739 harness/auth Tests; Vault/Router-Suiten | Teilweise |
| **L2 Synthetic Browser** | Lokales Chromium/Fixture, kein reales Kundenkonto | Auth-Benchmark 16 Szenarien `passed=true` | Teilweise |
| **L3 Live same-profile** | Echter Manager-Lauf: Run↔Lease↔CDP↔PID, ein Profil | **Fehlt** für AGY+BU; historisch nur ältere Commits | Erforderlich für DoD |
| **L4 Deployed** | Öffentliche/VCVM-Route, Container, systemd, Manifest | Health OK; Release `83cad83…`; ≠ HEAD | Erforderlich für Prod |

**Anti-Inflation:** Dateipräsenz allein ≤ Score 2. Unit-Grün inflatiert **keine** L3/L4-Claims.

---

## 3. Gesamtscore, Methodik, Konfidenz

### 3.1 Kennzahlen

| Kennzahl | Wert | Quelle |
| --- | ---: | --- |
| Feature-Anzahl | **26** (orthogonal, je 0–10) | `/tmp/cbm-plan-audit/11-pareto-score.md` |
| Summe | **129** / 260 | dieselbe |
| **PlanExecutionIndex** | **49,6 %** | \(129 / 260\) |
| Value-Weighted Index | **≈ 45,9 %** | \(\sum(v_i\cdot s_i)/(10\sum v_i)\); \(\sum v=154\), \(\sum(v\cdot s)=707\), \(707/1540\approx 0{,}459\) |
| Task-Mittel (T1–T11, grob) | **≈ 32,7 %** | Pareto-Audit (unabhängig vom Feature-Mittel) |
| Anforderungsmatrix (Body SoT) | **80** unique rows · must **69** / should **9** / research-only **2** · statuses **50** partial · **18** missing · **5** contradicted · **5** unknown · **2** research | parse of normalized matrix **body** in `/tmp/cbm-plan-audit/01-requirements-matrix.md` (not the contradictory header counts) |
| Plan-Checkboxen (offen / checked) | **66** / **0** | line-start list markers in masterplan; prose syntax mention excluded |

**Korrektur (2026-07-31):** Frühere Formulierungen mit \(\sum v=160\), Index ≈45,6 %, Matrix-Header „73 rows / 39 partial / 20 missing …“ und „67 Checkboxes“ sind **verworfen**. Maßgeblich sind die hier neu berechneten Werte und der **Matrix-Body** als Source of Truth (Header von `01` widerspricht dem Body).

### 3.2 Methodik (kurz)

1. Plan-Commit `1e1617e` als Soll-Spezifikation.
2. HEAD `c0d4f10` + **dirty** Worktree (uncommitted Agent-Family / CI-Diff) als Ist-Stand: **pending intentional deliverables**, **nicht** release-clean und **nicht** als shipped werten.
3. Unabhängige Fach-Audits (UI, Security, CI/Deploy, Harness/Auth, Git, VCVM, Tech-Katalog, Pareto).
4. NotebookLM nur als **sekundäre** Synthesehilfe (§4); Primat hat Repo/Runtime-Evidenz.
5. Feature-Scores (26×0–10, Summe 129) aus dem Pareto-Audit unverändert; Value-Gewichte neu summiert (\(\sum v=154\)).

### 3.3 Konfidenz

| Aussageklasse | Konfidenz | Begründung |
| --- | --- | --- |
| Ranking (was blockiert freigabe) | **Hoch** | Mehrere Audits konvergieren (AGY-Adapter, Contract, Deploy-Divergenz) |
| Exakter %-Index 49,6 | **Mittel–Hoch** | Skala audit-spezifisch; relative Ordnung stabil |
| L3/L4-Details | **Mittel** | Read-only; kein frischer same-profile-Lauf auf HEAD |
| NotebookLM-Zusatzrisiken ohne Repo-Beleg | **Verworfen** | siehe §4.2 |

---

## 4. NotebookLM-Methodik (sekundär)

### 4.0 Notebook-Identität (Proof)

| Feld | Wert |
| --- | --- |
| **Notebook ID** | `270598c3-713d-4188-81b8-0f0567c71572` |
| **Title (Proof)** | `VCVM Agent Orchestration Control Plan 2026-06-05` |
| **Source count at proof time** | **98** |
| **Proof file** | `/tmp/cbm-plan-audit/15-notebooklm-notebook-proof.json` (`updated_at` 2026-07-30T21:51:06Z) |
| **Conversation ID (IDR runs)** | `efa5a39c-b78b-4cca-b36c-459fd2259288` |

### 4.1 Durchgeführte NLM-Läufe

| Lauf | Artefakt | Conversation-ID | Quellen (Source-IDs) |
| --- | --- | --- | --- |
| Plan-IDR (primär spezifikationsnah) | `/tmp/cbm-plan-audit/12-notebooklm-idr.md` (+ `.json`) | `efa5a39c-b78b-4cca-b36c-459fd2259288` | u. a. `10d8f40e-c636-415a-af53-1ced4562e333` |
| Integriertes Tribunal-IDR | `/tmp/cbm-plan-audit/13-notebooklm-integrated-idr.md` (+ `.json`) | `efa5a39c-b78b-4cca-b36c-459fd2259288` | 10 Sources, u. a. `e2a3b923-…`, `efdca1e3-…`, `419d1d17-…`, `fbc9421f-…`, `ab16e7cf-…`, `41ceac4a-…`, `fbb764c9-…`, `10d8f40e-…`, `12e77699-…`, `50e89df8-…` |

NotebookLM (Notebook `270598c3-713d-4188-81b8-0f0567c71572`, 98 Sources zum Proof-Zeitpunkt) wurde mit **hochgeladenen Audit-Markdowns** gefüttert (Requirements, Harness/Auth, Security, CI/Deploy, VCVM, Git, Pareto u. a.). Es ersetzt **nicht** `git`/`pytest`/Runtime-Probes und bleibt **sekundär** gegenüber Repo/Runtime-Evidenz.

### 4.2 Explizit **abgelehnte** NLM-/Sekundär-Claims

Die folgenden Aussagen aus dem integrierten NLM-Entwurf (13) sind **ohne** Primärevidenz in den Fach-Audits **01–11 / Tech-Katalog** und werden **verworfen** (Halluzinations-/Übertragungsrisiko):

| Claim | Status | Grund |
| --- | --- | --- |
| **Lago**-AGPL-SaaS-Viraliät als CBM-Release-Blocker | **REJECT** | Lago kommt in Plan, Repo-Audits und Tech-Katalog **nicht** vor |
| **tldraw**-SDK-Lizenzverstoß / Excalidraw-Ersatzpflicht | **REJECT** | tldraw ist **kein** Plan-/Frontend-Dependency-Befund |
| **FIDO2-TPM physischer Lockout** als Top-Risiko Nr. 1 mit Hardware-Crash-Szenario | **REJECT als freigabeentscheidend** | Passkey-Handoff ist Plan-Policy; Auth-Audit beweist nur **synthetische** WebAuthn-Fixtures — kein TPM-Lockout-Messwert |
| Unspezifische „Fake-Green Catch-All schreiben PASS“ ohne Job-Bezug | **RELATIVIEREN** | CI-Audit: `\|\| true` nur an Cleanup/Log-Pfaden; nicht als flächendeckendes Fake-Green belegt |
| NotebookLM als Beweis für Remote-CI-Grün | **REJECT** | `gh run list` für `c0d4f10` war **leer** (CI-Deploy-Audit) |

**Beibehalten aus NLM nur**, wo es mit L0–L4-Audits übereinstimmt: HARD NO-GO, 49,6 %, Provider≠Harness-Widerspruch, fehlender AGY-Adapter, Deploy-Divergenz, MV3 ohne Extensions.

### 4.3 Operator-Tool vs. Plan-Katalog

Tech-Katalog: Plan nennt [roomi-fields/notebooklm-mcp](https://github.com/roomi-fields/notebooklm-mcp); Host nutzt **`nlm` 0.9.4** aus [jacob-bd/notebooklm-mcp-cli](https://github.com/jacob-bd/notebooklm-mcp-cli). Beides **Research-only**, inoffizielle Google-Integration.

---

## 5. Identitäten: Branch / Commit / Deploy-Divergenz

### 5.1 Git-Linie (auditverifiziert)

```text
… → 36ef087 (profile-share + vault)
      → 827c0f0  ← Plan „verified head“ / CI fail-closed
        → 1e1617e  ← Plan-Dokument (nur Masterplan-Markdown)
          → f0a2410  ← Auth Passkey/OAuth-Benchmark
            → c0d4f10  ← aktueller HEAD dieses Worktrees
```

| Beziehung | Ergebnis |
| --- | --- |
| `827c0f0` Parent von `1e1617e` | Ja |
| `1e1617e` Ancestor von `c0d4f10` | Ja |
| Plan-Branch-Name im Plantext | `feature/secure-action-recorder-vault` @ `827c0f0` |
| Tatsächlicher Orca-Worktree-Branch | `Martin-Hausleitner/cbm-acpx-agent-family` @ `c0d4f10` |
| `c0d4f10` in `main` | **Nein** (Git-Lineage-Audit) |
| Dirty Worktree | **Ja — nicht release-clean**: modified `.github/workflows/ci.yml` (+ Agent-Family-Job); untracked Agent-Family/Soniox/Skill. Das sind **intentionale pending Deliverables** (Side-Lane), **kein** Beweis für sauberen Release-Stand und **kein** Widerspruch zum HARD NO-GO |

### 5.2 Deployed VCVM (L4)

| Signal | Beobachtung | Match HEAD `c0d4f10`? |
| --- | --- | --- |
| Public Health `https://vcvm.tail6a40cd.ts.net/health` | `{"ok":true}` | n/a (kein Commit) |
| `/api/version` | **404** | Commit nicht exponiert |
| Release-Manifest | `release-20260728-83cad83-grid`, Source-Commit `83cad83…` | **Nein** |
| Deploy-Source / Image-Korrelation | u. a. Tree `70433ca` bzw. `e16f793` (Audits 06/09) — **älter** als `827c0f0`/`c0d4f10` | **Nein** |
| Manager-Container | healthy, `127.0.0.1:18115→8080` | Runtime ≠ Plan-HEAD |
| Browser-Use-Worker | active, u. a. `--llm-provider grok-cli` | **Nicht** Antigravity |
| ACPX-Service | **inactive/stale** | **Nein** Ready |
| Live-Profil „VCVM Mobile Demo“ | running, VNC 6100; `extension_ids=[]`; Prozess **`--disable-extensions`** | MV3 **nicht** live |
| Browser-Tools / Provider-Readiness (API) | unbrowse/stagehand/browser-harness **stale**; antigravity **protocol_unavailable**/stale | L4 ≠ L0 Probe allein |
| FPS/RTT Live-Metrics | `null` | Kein L4-Perf-Beweis |
| Streaming-Report | `docs/streaming-benchmark-latest.md` vom **2026-07-21** | veraltet |

**Schluss:** Jede UI-/Harness-Erfolgsmeldung, die Commits wie `70ceccd` (29.07.-Report) zitiert, ist **historisch**, nicht freigabetauglich für `c0d4f10`.

---

## 6. Status-Tabellen: completed / partial / missing / contradicted

### 6.1 Completed (L1+ ausreichend; L3/L4 nicht behauptet)

| Thema | Schicht | Beleg |
| --- | --- | --- |
| ACPX-Versionspin 0.12.1 + SDK 1.2.1 + Lock-Skript | L0+L1 | `acpx_runtime_lock.py` → `ok: true` |
| Vault **Non-Reveal** Agent-Oberfläche | L0+L1 | `FORBIDDEN_AGENT_OPERATIONS`, Tests grün |
| Profile-Share **synthetisch** (keine echten Cookies/Passkeys) | L1+L2 | profile-share Tests/E2E-Suite |
| `allow_second_browser=false` im Modell/Router | L0+L1 | `TaskRunCreate` / Router-Tests |
| Geordnete Tool-Kette Unbrowse→Stagehand→Browser-Harness (Contract) | L0+L1 | Router + Models |
| Auth-Benchmark synthetisch (Passwort/OTP/OAuth/Passkey-Fixtures) | L2 | `artifacts/auth-benchmark/report.json` `passed=true`; 127 Tests |
| Secure-Recorder Ingest (HMAC, Origin, Secret-Reject) unit-seitig | L1 | Security-Audit 77 focused passed |
| Extension-Bridge Origin + Token-0600 unit-seitig | L1 | bridge security tests |

### 6.2 Partial

| Thema | Lücke |
| --- | --- |
| CI fail-closed / Secret-Scan redacted | gitleaks 2 Hits (synthetische Fixtures); Node CI `20` vs ACPX `≥22.13`; Remote-CI für HEAD leer |
| UI compact / Settings-first | Code stark; `node_modules`/Vitest fehlten im UI-Audit; Screenshots stale |
| Provider/Harness-UI | CALLABLE schließt AGY aus; ProfileHarness listet AGY weiter |
| ACPX Worker/Runner | Unit stark; Service deployed **inactive** |
| Adapter Unbrowse/Stagehand/Browser-Harness | Unit/mocks; installierte Live-CDP-E2E offen |
| MV3 Recorder | Bridge unit; Session/Command-Binding unvollständig; Real-Chromium E2E nur Sibling `42c018f` |
| Vault Use-Grants | Namen `authorize_use`/`revoke_use`; kein voller prepare/fill-Lifecycle |
| Release-Skripte | Dry-Run/Transaction-Tests; Live-Rollback unbewiesen; `/tmp`-Assertion fail |
| Agent-Family Quality Gate | Tests grün, **uncommitted** |

### 6.3 Missing (HEAD)

| Thema | Fehlt |
| --- | --- |
| Antigravity→Browser-Use Chat-Model | `scripts/antigravity_cli_chat_model.py` + Tests |
| Same-Profile AGY+BU E2E | `e2e/tests/test_browser_use_antigravity_manager_e2e.py`, Runner |
| Observability auf HEAD | `backend/observability/`, `cbm_trace_ctl.py`, OBS-Doku |
| Plan-Release-Report | `docs/reports/VCVM-STABLE-BROWSER-USE-ANTIGRAVITY-RELEASE-2026-07-30.md` |
| CLI `--provider` für Task-10-Shape | `cbm_agent_ctl.py tasks run` ohne `--provider` |
| Frische L3-Korrelation Run/Lease/CDP/PID | kein Receipt am HEAD |
| Deploy = HEAD | Manifest/Source ≠ `c0d4f10` |

### 6.4 Contradicted

| Plan-Soll | Ist | Schwere |
| --- | --- | --- |
| Provider und Harness unabhängig; `provider=antigravity` + `harness=browser-use` | Routing nur bei `harness=acpx`; AGY oft noch Harness-Metadaten | **P0** |
| AGY steuert BU oder sichtbar unavailable; nie still ersetzen | Legacy-E2E mappt AGY-Mode → **Claude**; Worker ohne AGY | **P0** |
| Feature-Matrix %-Zahlen als „Current verified state“ | Historische Schätzungen; **nicht** re-verifiziert am HEAD | **P1** (Doku) |
| Deployed Runtime = Plan-Branch | Deploy älter / anderer SHA | **P0** Release |
| MV3 Recorder live | `--disable-extensions` | **P0** Security-E2E |
| Shortcut-Map Plan vs Mobile | Code: Ctrl+J/B/G/K ≠ Plan Cmd+B / Shift+F … | **P2** UX |
| `prepare_use`/`fill_receipt` | Code: `authorize_use`/`receipt` (future) | **P2** Spec-Drift |

---

## 7. Feature-Score-Matrix (26 Features, 0–10)

Primärkategorie je Feature (keine Doppelzählung). Scores aus Pareto-Audit 11; hier mit Schichtenbezug.

| ID | Feature | Kat. | Score | Konf. | Schicht max. | Kurzbeleg |
| --- | --- | --- | ---: | --- | --- | --- |
| F01 | CI fail-closed / Secret-Artefakte | security | 8 | hoch | L1 | package-artifact needs project-gates; redacted scan |
| F02 | Release-Remote-Assertion ehrlich | test | 6 | hoch | L1 | `/tmp` in unit_text schlägt fehl |
| F03 | Provider≠Harness API | implementation | 5 | hoch | L1 | Routing acpx-only |
| F04 | Provider≠Harness UI | implementation | 4 | mittel | L0 | ProfileHarness enthält AGY |
| F05 | Compact Browser-Use UI | implementation | 5 | mittel | L0 | Komponenten; Browser-Proof stale |
| F06 | AGY→BU Adapter | implementation | 1 | hoch | — | Dateien fehlen |
| F07 | AGY-Readiness-Ehrlichkeit | implementation | 4 | hoch | L0 | Probe ≠ Adapter; Legacy Claude |
| F08 | ACPX Runtime-Pin | implementation | 9 | hoch | L1 | Lock ok 0.12.1 |
| F09 | Tool-Router / no 2nd browser (Unit) | test | 8 | hoch | L1 | Router-Tests grün |
| F10 | Installierte Harnesses Live-E2E | e2e | 3 | mittel | L3 hist. | Report 29.07. @ altem Commit |
| F11 | MV3 Local-Control Security (Unit) | security | 8 | hoch | L1 | Origin/0600 |
| F12 | Real Chromium MV3 E2E | e2e | 1 | hoch | — | Suite nicht auf HEAD |
| F13 | Vault Non-Reveal | security | 9 | hoch | L1 | Tests + Contract |
| F14 | Use-Grant Lifecycle | implementation | 3 | hoch | L0 | unvollständig |
| F15 | Profile-Share synthetisch | test | 8 | hoch | L2 | E2E grün |
| F16 | OTel/TokScale metadata-only | implementation | 0 | hoch | — | fehlt auf HEAD |
| F17 | CDP/VNC Latenz-Evidenz | stability | 3 | mittel | L0 | Report 21.07. |
| F18 | Mobile UI Gates | test | 7 | hoch | L1 | Gate-Tests grün |
| F19 | Same-Profile AGY+BU E2E | e2e | 0 | hoch | — | fehlt |
| F20 | Legacy AGY ACPX E2E Ehrlichkeit | e2e | 2 | hoch | L1 | Claude-Mapping |
| F21 | VCVM Release/Rollback Readiness | implementation | 4 | mittel | L1 | Dry-run; Unit-Fails |
| F22 | Release-Docs/Screenshots @ Commit | documentation | 2 | hoch | L4 hist. | Plan-Release-Report fehlt |
| F23 | Auth-Benchmark | security | 8 | hoch | L2 | passed report |
| F24 | Agent-Family Quality Gate | implementation | 6 | mittel | L1 | untracked, Tests grün |
| F25 | BU Worker Multi-Provider (ohne AGY) | implementation | 6 | hoch | L1 | cursor/claude/grok |
| F26 | Masterplan-Dokumentqualität | documentation | 9 | hoch | L0 | Spezifikation stark |

**Kategorie-Mittel:** Security 8,25 · Test 7,25 · Docs 5,50 · Implementation 4,27 · Stability 3,00 · Live-E2E **1,50**.

---

## 8. Technologie-Link-Korrekturen (Katalog)

Aus `/tmp/cbm-plan-audit/10-tech-catalog.md` — **Entscheidungen Adopt/Optional/Defer bleiben**; Texte schärfen:

| Eintrag | Plan-Text | Korrektur |
| --- | --- | --- |
| Unbrowse | „Verify license“ | **MIT** verifiziert |
| browser-harness-js | „Verify license“ | **MIT**; Defer bleibt Produktentscheid |
| OpenClaw | „Project license“ | **MIT** (+ Third-Party Notices) |
| CBM Fork/Upstream | „Project license“ | **MIT nur GUI-Source**; Binary separat |
| Bitwarden | „GPL family“ | **GPL-3.0 + Bitwarden License v1** für EE-Pfade |
| KeePassXC | „GPL family“ | **GPL-2 or GPL-3** |
| NotebookLM | roomi-fields | Zusätzlich: installiertes **`nlm`/jacob-bd**; Research-only |
| ACPX | Pin 0.12.1 | Pin **korrekt**; npm latest 0.13.0 — absichtlich gepinnt |
| Browser Use | 0.13.6 Pin | PyPI 0.13.7; in Default-Python oft **nicht** importierbar |
| Stagehand | Adopt local | Adapter da; `stagehand_runtime/node_modules` oft **fehlend** |

**Keine** erzwungene Umklassifizierung (Defer→Adopt) allein aus Link-Status.

---

## 9. Agent-Family-Arbeit in diesem Branch (orthogonal)

| Artefakt | Zustand | Bewertung |
| --- | --- | --- |
| `scripts/agent_family_pipeline.py` / `quality_gate.py` / `budget_gate.py` | **untracked** | deterministische Gates; Quality-Report 8/8 in Audit |
| `scripts/soniox_stt_adapter.py` + Tests | **untracked** | Contract-Adapter, nicht Masterplan-Task |
| `.agents/skills/acpx-agent-family/` | **untracked** | Skill: Plan→Forge→Gates→IDR→Tribunal→Release |
| CI-Job `agent-family-quality-gate` | **uncommitted** Diff in `ci.yml` | kollidiert potenziell mit Plan-Task-1 CI-Ownership |
| Masterplan Tasks 1–11 | **erwähnt Agent-Family nicht** | Side-Lane auf Auth-Passkey-Base |

**Urteil:** Wertvolle **Harness-für-Agenten-DAG**-Arbeit, aber **kein Ersatz** für Masterplan-Wellen 2/6. Darf freigabe nicht als „Plan 50 % erledigt“ verkaufen.

### 9.1 OpenCode / Soniox — Grenzen

| Thema | Limit |
| --- | --- |
| OpenCode | Im Agent-Family-Skill als `acpx:opencode` erlaubt; **kein** Beweis, dass OpenCode denselben Manager-Lease/CDP wie Browser Use teilt |
| Soniox | Adapter/Contract-Tests untracked; **kein** STT-Produkt-DoD im Masterplan; keine Live-Audio-E2E in Audits |
| Execute-Mode Agent-Family | Fail-closed ohne absolute Permission-Policy/MCP-Dateien — korrekt, aber **nicht** VCVM-Prod-Beweis |
| Concurrency | Hard-Cap 4 in Skill — gut; ersetzt nicht Browser-Prozess-Invariante |

---

## 10. Top-10 Risiken (auditgestützt)

| # | Risiko | Schicht | Schwere |
| ---: | --- | --- | --- |
| 1 | **Falsche Prod-Claims** trotz Deploy≠HEAD und fehlendem AGY+BU-Pfad | L4/DoD | P0 |
| 2 | **Provider/Harness-Widerspruch** blockiert Plan-Soll-Payload | L0/L1 | P0 |
| 3 | **Silent Substitute** (Legacy AGY→Claude) verletzt DoD-Ehrlichkeit | L1/E2E | P0 |
| 4 | **MV3 ungetestet live** (`--disable-extensions`) | L4 | P0 |
| 5 | **Extension Session nicht command-gebunden** (Replay/Confused Session) | L0 | P1–P2 |
| 6 | **OTLP SSRF / Spool-Rechte** auf Sibling-Branch vor Merge | branch-only | P1 (Merge-Stop) |
| 7 | **gitleaks** schlägt auf Auth-Fixtures → Release-Gate rot | L1/CI | P1 |
| 8 | **CI Node 20** vs ACPX Engine ≥22.13 | L0 | P1 |
| 9 | **Zweiter Browser / Attach-only** nur unit-bewiesen | L1 vs L3 | P1 |
| 10 | **Agent-Family uncommitted** + CI-Ownership-Kollision | Prozess | P2 |

*(Abgelehnte NLM-Risiken Lago/tldraw/TPM: §4.2.)*

---

## 11. Pareto-Prioritäten

### 11.1 Value-Gap (Features)

Top-Lücken nach \(v(10-s)/10\) (aus 11-pareto-score):

1. **F19** Same-Profile AGY+BU E2E
2. **F06** AGY-Adapter
3. **F12** MV3 Chromium E2E
4. **F10** Installierte Harness Live
5. **F04/F03** UI/API Provider≠Harness
6. **F21/F22** Release + Docs
7. **F07** Readiness-Ehrlichkeit
8. **F14** Use-Grants
9. **F16/F17** Tracing / Latenz

~80 % der gewichteten Restlücke: Features F19…F17/F05-Band (siehe Pareto-Audit).

### 11.2 Tasks (20 %-Kern)

| Prio | Task | Wirkung |
| --- | --- | --- |
| P0 | **T3** Provider/Harness-Trennung | schaltet ehrlichen Contract frei |
| P0 | **T4** Antigravity-Provider | Produktkern |
| P0-Proof | **T10** Same-Profile E2E | macht T3+T4 freigabetauglich |
| P1 | T6/T7 residual, T1 residual, T11 | Security + Ship |
| P2 | T2/T5/T9 | UX/Perf |
| P3 | T8 Tracing | nach Privacy-Fix, optional MVP |

---

## 12. Genau 20 actionable Tickets

Legende Owner-Lane: `api` · `worker` · `ui` · `ext` · `sec` · `ci` · `deploy` · `e2e` · `docs` · `obs` · `family`

Jedes Ticket: Dependencies, Acceptance, metrics-sicheres Artefakt, Stop/Rollback-Gate.

### TKT-01 — Provider≠Harness API freischalten
- **Owner:** `api` · **Deps:** —
- **Acceptance:** `POST` Task-Run mit `harness=browser-use` + `provider={id:antigravity,transport:cli}` + `allow_second_browser=false` validiert (201/akzeptiert oder ehrlich 422 nur bei fehlender Readiness, **nicht** wegen Harness-Kopplung).
- **Artefakt:** `artifacts/ci/provider-harness-contract-junit.xml` (nur Counts/Status).
- **Stop/Rollback:** Bei Regression `allow_second_browser=true` akzeptiert → sofort revert Models-Validator.

### TKT-02 — UI/Profil-Vokabular entkoppeln
- **Owner:** `ui` · **Deps:** TKT-01
- **Acceptance:** `antigravity` nicht mehr als `ProfileHarness`-Callable; Settings zeigt Provider separat; Reload ändert Route nicht still.
- **Artefakt:** `artifacts/ci/frontend-harness-options.json` (`passed`, test counts).
- **Stop:** Doppelte Provider-Matrix im Chat → UI-Gate fail.

### TKT-03 — Legacy `harness=antigravity` lesen, neu normalisieren
- **Owner:** `api`+`ui` · **Deps:** TKT-01
- **Acceptance:** Alte Metadaten lesbar; neue Writes speichern getrennte Felder; Migrationstests grün.
- **Artefakt:** `artifacts/ci/harness-migration-junit.xml`.
- **Stop:** Datenverlust bei Migration → Rollback Migration.

### TKT-04 — `AntigravityCLIChatModel` fail-closed
- **Owner:** `worker` · **Deps:** TKT-01
- **Acceptance:** Suite `test_antigravity_cli_chat_model` (stdin, argv, schema, timeout, cancel, redaction, `protocol_unavailable`).
- **Artefakt:** `artifacts/ci/agy-adapter-junit.xml`.
- **Stop:** Shell-Interpolation oder Secret in Logs → hard fail merge.

### TKT-05 — Browser-Use-Worker Allowlist + kein Zweitbrowser
- **Owner:** `worker` · **Deps:** TKT-04
- **Acceptance:** `antigravity-cli` nur nach Readiness; CDP nur Manager-Capability; Disconnect killt Manager-Browser nicht.
- **Artefakt:** `artifacts/ci/bu-worker-provider-junit.xml`.
- **Stop:** Zweiter Browser-PID im Test → fail.

### TKT-06 — Same-Profile AGY+BU E2E (synthetisch)
- **Owner:** `e2e` · **Deps:** TKT-05, TKT-01
- **Acceptance:** Korrelation Run/Worker/Profile/Lease/CDP-Target/PID; kein Zweitprozess; ehrliche Failure-Matrix (missing binary, auth, origin).
- **Artefakt:** `artifacts/e2e/agy-bu-same-profile.json` (IDs, outcomes, **keine** Secrets).
- **Stop/Rollback:** Bei Lease-Leak automatisches revoke + Profil-Cleanup-Skript.

### TKT-07 — Legacy AGY→Claude E2E entgiften
- **Owner:** `e2e` · **Deps:** TKT-02
- **Acceptance:** Legacy-Test markiert `stale` / gesplittet; nicht release-required; kein stiller Claude-Ersatz als „AGY success“.
- **Artefakt:** `artifacts/ci/stale-provider-compat-junit.xml`.
- **Stop:** Release-Job hängt an Legacy-Test → umhängen.

### TKT-08 — CI Node ≥22.13 für ACPX-Pfade
- **Owner:** `ci` · **Deps:** —
- **Acceptance:** Workflow nutzt Node, das `engines` erfüllt; Lock-Job grün.
- **Artefakt:** `artifacts/ci/acpx-lock-report.json`.
- **Stop:** Pin-Drift → package-artifact blockiert.

### TKT-09 — Release-Remote `/tmp`-Assertion fixen
- **Owner:** `ci` · **Deps:** —
- **Acceptance:** `test_vcvm_release_remote` grün; Assertion nur `Environment=PATH=` laut Plan.
- **Artefakt:** `artifacts/ci/release-remote-junit.xml`.
- **Stop:** Assertion wieder zu breit → fail.

### TKT-10 — gitleaks Auth-Fixtures entschärfen
- **Owner:** `sec` · **Deps:** —
- **Acceptance:** `gitleaks detect --exit-code 1` clean **oder** reviewed allowlist mit Begründung; Tests bleiben aussagekräftig.
- **Artefakt:** `artifacts/ci/gitleaks-summary.json` (counts only, redacted).
- **Stop:** Echte Secrets → commit reject.

### TKT-11 — Remote-CI für exakten Release-Commit
- **Owner:** `ci` · **Deps:** TKT-08–10, clean tree
- **Acceptance:** `gh` run für SHA grün; Status API nicht leer.
- **Artefakt:** Link + `artifacts/ci/remote-ci-summary.json` (`conclusion`, `sha`).
- **Stop:** Rot → kein Deploy.

### TKT-12 — MV3 Session/Command-Binding
- **Owner:** `ext` · **Deps:** —
- **Acceptance:** Session bindet extension-id, nonce, op; Replay/wrong-session fail Tests.
- **Artefakt:** `artifacts/ci/extension-bridge-junit.xml`.
- **Stop:** Origin-Bypass → merge block.

### TKT-13 — Real Chromium MV3 E2E (Sibling mergen oder reimplement)
- **Owner:** `e2e`+`ext` · **Deps:** TKT-12
- **Acceptance:** Disposable Chromium lädt Extension; status/start/stop/export/compile; redacted artifacts; token 0600.
- **Artefakt:** `artifacts/e2e/mv3-devtools-report.json`.
- **Stop/Rollback:** Extension-Crash → Feature-Flag off.

### TKT-14 — Deployed Browser mit Extensions für Proof-Profil
- **Owner:** `deploy` · **Deps:** TKT-13
- **Acceptance:** Proof-Profil **ohne** `--disable-extensions`; extension_ids nicht leer; Health ok.
- **Artefakt:** `artifacts/deploy/extension-runtime-proof.json` (ids/digests only).
- **Rollback:** vorheriges Release-Manifest.

### TKT-15 — Vault Use-Grant Lifecycle (non-reveal)
- **Owner:** `sec`+`api` · **Deps:** —
- **Acceptance:** prepare/authorize + receipt + revoke; negative Tests no reveal; Namensklärung im Plan.
- **Artefakt:** `artifacts/ci/vault-grants-junit.xml`.
- **Stop:** Reveal-Pfad → hard fail.

### TKT-16 — Installierte Tool-Preflights (Unbrowse/Stagehand/BH)
- **Owner:** `worker` · **Deps:** —
- **Acceptance:** Missing binary → typed unavailable; bei Install: attach-only an Manager-CDP.
- **Artefakt:** `artifacts/e2e/browser-tools-readiness.json`.
- **Stop:** `opened_second_browser` → fail.

### TKT-17 — ACPX Service readiness auf VCVM
- **Owner:** `deploy` · **Deps:** TKT-08
- **Acceptance:** Presence nicht stale; Preflight agents ehrlich; optional eine synthetic MCP-Action.
- **Artefakt:** `artifacts/deploy/acpx-presence.json`.
- **Rollback:** Unit stop; Browser-Use unberührt lassen.

### TKT-18 — Streaming/CDP/VNC Metrics frisch
- **Owner:** `deploy`+`docs` · **Deps:** —
- **Acceptance:** Report mit connect/first-frame/p95; unavailable ehrlich; HEAD im Report.
- **Artefakt:** `docs/streaming-benchmark-latest.md` + `artifacts/bench/streaming.json`.
- **Stop:** Keine synthetischen Timings für missing tools.

### TKT-19 — Observability nur nach Privacy-Gate (optional MVP)
- **Owner:** `obs` · **Deps:** Privacy-Review
- **Acceptance:** `content_capture=false`; Spool 0700/0600; OTLP Allowlist/SSRF-Tests; sonst **nicht** mergen.
- **Artefakt:** `artifacts/ci/observability-privacy-junit.xml`.
- **Stop:** SSRF-Test rot → kein Merge (Sibling-Stand).

### TKT-20 — Transaktionales Deploy + Rollback-Receipt + Screenshots
- **Owner:** `deploy`+`docs` · **Deps:** TKT-06,11,13,14,17
- **Acceptance:** Clean worktree; Deploy-SHA = UI-Screenshot-SHA; Rollback dry **und** mind. eine echte Rollback-Übung mit Receipt; DoD-Checkliste.
- **Artefakt:** `docs/reports/VCVM-STABLE-BROWSER-USE-ANTIGRAVITY-RELEASE-2026-07-30.md` + `artifacts/deploy/*-receipt.json`.
- **Rollback-Gate:** Automatisch vorheriges `release-*` wenn Health fail.

---

## 13. Release Decision Tree

```text
Start: Claim „Stable AGY+BU Production“?
│
├─ HEAD clean + Remote-CI grün für exakten SHA?
│    └─ Nein → NO-GO (TKT-11)
│
├─ Provider≠Harness API+UI (TKT-01..03) grün?
│    └─ Nein → NO-GO
│
├─ AGY Adapter + Worker (TKT-04..05) grün ODER UI zeigt protocol_unavailable ohne Ersatz?
│    └─ Nein / stiller Ersatz → NO-GO
│
├─ L3 Same-Profile Receipt (TKT-06) grün?
│    └─ Nein → NO-GO
│
├─ Vault non-reveal + grants policy (TKT-15) + gitleaks clean (TKT-10)?
│    └─ Nein → NO-GO
│
├─ MV3 unit+real E2E (TKT-12..13) und Deploy-Proof nicht disable-extensions (TKT-14)?
│    └─ Nein → NO-GO für „Secure Recorder Release“
│
├─ Deploy Manifest SHA == freigegebener SHA + Rollback-Receipt (TKT-20)?
│    └─ Nein → NO-GO Production
│
└─ Ja auf allen Pfaden → GO (nur dann)
```

**Heute:** Abbruch bereits bei den ersten drei Nein-Zweigen.

---

## 14. Aktualisierte Six-Wave Roadmap

| Welle | Fokus | Exit-Kriterium (evidenzgewichtet) | Ticket-Band | Status jetzt |
| ---: | --- | --- | --- | --- |
| **0** | CI-Truth & Artifact-Safety | Remote-CI grün; gitleaks clean; Node/ACPX konsistent; keine Secret-Uploads | TKT-08–11 | **Partial** |
| **1** | Compact UI + ehrliche Selectors | Frontend-Tests+Build; Mobile-Gates; Screenshots @ HEAD; Provider≠Harness UI | TKT-02–03 + UI-Gates | **Partial** |
| **2** | Antigravity als BU-Provider | Adapter+Worker+sichtbare Unavailable; **kein** Claude/Grok-Silent-Sub | TKT-04–05,07 | **Missing** |
| **3** | ACPX + ordered tools ehrlich | ACPX presence; installierte Tools unavailable/ready; Router L3 optional | TKT-16–17 | **Partial** |
| **4** | Recorder + Vault + Profile | MV3 real E2E; Use-Grants; synthetic share bleibt ehrlich | TKT-12–15 | **Partial** |
| **5** | Tracing + Performance | Privacy-reviewed OTel **oder** bewusst deferred; frische Latenz-Metriken | TKT-18–19 | **Missing / deferred OK** |
| **6** | VCVM Release | Transaction+Rollback+Screenshots+SHA-Match Public Route | TKT-20 | **Blocked** |

**Parallel erlaubt** nur bei disjunkter File-Ownership (Plan §10); Integration-Owner-Dateien serialisieren.

---

## 15. Quellenverzeichnis (Primär)

| ID | Pfad |
| --- | --- |
| A01 | `/tmp/cbm-plan-audit/01-requirements-matrix.md` |
| A03 | `/tmp/cbm-plan-audit/03-ui-ux-audit.md` |
| A04 | `/tmp/cbm-plan-audit/04-harness-auth-audit.md` |
| A05 | `/tmp/cbm-plan-audit/05-security-vault-extension-audit.md` |
| A06 | `/tmp/cbm-plan-audit/06-ci-deploy-runtime-audit.md` |
| A07 | `/tmp/cbm-plan-audit/07-repo-evidence-replacement.md` |
| A08 | `/tmp/cbm-plan-audit/08-git-lineage.md` |
| A09 | `/tmp/cbm-plan-audit/09-vcvm-runtime.md` |
| A10 | `/tmp/cbm-plan-audit/10-tech-catalog.md` |
| A11 | `/tmp/cbm-plan-audit/11-pareto-score.md` |
| A12 | `/tmp/cbm-plan-audit/12-notebooklm-idr.md` / `.json` |
| A13 | `/tmp/cbm-plan-audit/13-notebooklm-integrated-idr.md` / `.json` |
| A15 | `/tmp/cbm-plan-audit/15-notebooklm-notebook-proof.json` (notebook `270598c3-713d-4188-81b8-0f0567c71572`, 98 sources) |
| Plan | [`docs/superpowers/plans/2026-07-30-stable-browser-use-antigravity-masterplan.md`](../superpowers/plans/2026-07-30-stable-browser-use-antigravity-masterplan.md) |

---

## 16. Tribunal-Schlussformel

> **HARD NO-GO** für Production des Masterplan-Ziels „Stable Browser-Use + Antigravity Control Plane“ am HEAD `c0d4f10` und am deployeden VCVM-Stand.
> **GO** nur für gezielte Umsetzung der Tickets TKT-01…20 unter den Stop-Gates.
> Plan-%-Matrix und historische VCVM-Reports sind **nicht** als verifizierte Wahrheit für diesen HEAD zu lesen — siehe Execution-Audit-Addendum im Masterplan.

---

*Ende IDR/Tribunal · 2026-07-30 (Zahlenkorrektur 2026-07-31) · Synthese ohne Code-Änderungen außerhalb der beauftragten Docs.*
