# Paperclip-gestützte Browser-Zugriffskontrolle

Stand: 2026-07-20 · Status: Architekturvorschlag für die nächste Implementierungsphase

## Kurzentscheidung

Der aktuelle einzelne `AUTH_TOKEN` schützt den Manager als Ganzes, trennt aber weder Menschen noch Agenten noch einzelne Browserprofile. Für freigegebene Browserzugriffe soll daher eine kleine, lokal durchsetzbare Policy-Schicht entstehen. Sie ist mit Paperclip-Agenten kompatibel, hängt aber nicht davon ab, dass ein Paperclip-Server erreichbar ist.

Das entscheidende Prinzip lautet: **Die API erzwingt Zugriff pro Sandbox und Profil. Das Dashboard zeigt nur die bereits zulässigen Daten.** Eine versteckte UI-Schaltfläche oder ein alleiniger Frontend-Filter wäre keine Sicherheitsgrenze.

Paperclip verwendet ebenfalls zentrale, explizite Berechtigungen und strukturierte Scopes für Agenten und Menschen. Dieser Vorschlag übernimmt dieses Muster für Browser-Sandboxes, statt einen weitreichenden Besitzer-Token an Agenten weiterzugeben. Siehe die offiziellen [Paperclip-Informationen zu Agent-Berechtigungen](https://docs.paperclip.ing/reference/api/agents/) und die [Paperclip-Architekturübersicht](https://github.com/paperclipai/paperclip).

## Zielbild

```text
Board-Admin ──────┐
                  │ verwaltet Identitäten und Sandbox-Grants
Human-Operator ───┼──> CloakBrowser Policy API ──> Profil / VNC / CDP
                  │              │
Paperclip-Agent ──┘              └──> Audit-Ereignisse

Sandbox „research“ ──> Profile A, Profile B
Sandbox „payments“ ──> Profile C
```

Ein Profil gehört genau zu einer Sandbox. Ein Mensch oder Agent erhält eine explizite Berechtigung für eine oder mehrere Sandboxes. Ein Zugriff ist nur dann möglich, wenn die Berechtigung sowohl das Profil als auch die angefragte Aktion abdeckt.

## Rollen und Aktionen

| Rolle / Credential | Sichtbar | VNC bedienen | Start/Stop | CDP-Automation | Policies verwalten |
| --- | --- | --- | --- | --- | --- |
| Owner/Admin | alle erlaubten Profile | ja | ja | ja | ja |
| Operator | zugewiesene Sandboxes | ja | ja, wenn erteilt | nein, außer explizit | nein |
| Viewer | zugewiesene Sandboxes | nur ansehen | nein | nein | nein |
| Paperclip-Agent | nur zugewiesene Sandboxes | optional | optional | nur bei `automate` | nein |

Die konkrete Berechtigung ist nicht nur eine Rollenbezeichnung. Sie besteht aus einem Scope (`sandbox_id`) und einer Aktion:

- `view` — Profilmetadaten in Minimalform und VNC-Bild empfangen.
- `interact` — VNC-Eingaben und Clipboard-Bridge für die zugewiesene Sandbox.
- `operate` — Browser in der zugewiesenen Sandbox starten oder stoppen.
- `automate` — CDP-REST- und WebSocket-Zugriff für einen Agenten.
- `admin` — Profile, Identitäten und Grants verwalten.

`interact` schließt `view` ein; `operate` schließt `interact` ein. `automate` ist bewusst separat, weil CDP vollen Zugriff auf den Browserinhalt und seine Ausführung ermöglicht.

## Durchsetzungspunkte

Alle folgenden Pfade müssen dieselbe serverseitige Entscheidung verwenden:

1. Profil-Liste und Profil-Detail: nicht erlaubte Profile werden nicht aufgelistet und liefern bei Direktaufruf keine Daten.
2. VNC-WebSocket: ein Viewer erhält nur Server-Frames; Client-Eingaben werden vor KasmVNC verworfen. Ein nicht berechtigter Client wird vor dem VNC-Handshake geschlossen.
3. Clipboard: Lesen und Schreiben erfordert mindestens `interact`, weil Clipboard-Inhalte sensibel sind.
4. CDP-REST und CDP-WebSockets: erfordern explizit `automate`; ein View-Grant reicht nicht.
5. Profil ändern, löschen, Start/Stop sowie Policies verwalten: nur nach `operate` beziehungsweise `admin`.

Damit bleibt ein direkter Aufruf von `/api/profiles/{id}/vnc`, `/clipboard` oder `/cdp` genauso geschützt wie der sichtbare Dashboard-Workflow.

## Identitäten und Secrets

### Menschen

- Nutzername, Passwort-Hash und Aktiv-Status liegen lokal in SQLite.
- Passwörter werden mit einem speicherharten Verfahren gehasht; niemals Klartext speichern oder protokollieren.
- Die Browser-Session ist ein kurzlebiges, signiertes, `HttpOnly`- und `SameSite=Strict`-Cookie.
- Der bestehende `AUTH_TOKEN` bleibt ausschließlich ein Bootstrap-Admin/Notfallzugang und wird nicht an reguläre Nutzer oder Agenten verteilt.

### Paperclip-Agenten

- Jeder Agent bekommt einen eigenen, zufällig erzeugten Bearer-Key.
- In der Datenbank liegt nur ein kryptografischer Hash; der Klartext wird bei Erzeugung oder Rotation genau einmal angezeigt.
- Der Key ist an `paperclip_agent_id`, einen Namen, Aktiv-Status und konkrete Sandbox-Grants gebunden.
- Rotation oder Deaktivierung muss die Berechtigung unmittelbar beenden.
- Der Key gehört in den Secret Store der Laufzeit, nicht in Git, Aufgabenbeschreibungen, Screenshots oder Browser-Chatverläufe.

Paperclip selbst dokumentiert Agent API Keys als gehasht gespeicherte Credentials; die lokale Browser-Policy folgt dem gleichen Least-Privilege-Prinzip. [Paperclip AGENTS.md](https://github.com/paperclipai/paperclip/blob/master/AGENTS.md)

## Minimaler Datenentwurf

```text
profiles
  └── sandbox_id TEXT NOT NULL DEFAULT 'default'

access_users
  ├── id, username, password_hash, role, active, created_at

access_agents
  ├── id, paperclip_agent_id, display_name, key_hash, active, created_at

access_grants
  ├── principal_type ('user' | 'agent')
  ├── principal_id
  ├── sandbox_id
  └── permission ('view' | 'interact' | 'operate' | 'automate')

access_audit_events
  └── actor, action, sandbox_id, profile_id, outcome, timestamp
```

Die erste Version benötigt keine externe Identitätsplattform und keine zusätzliche Datenbank. Sie muss jedoch per `ACCESS_CONTROL_ENABLED=1` bewusst aktiviert werden. Ohne Aktivierung bleibt das bestehende Einzel-Token-Verhalten rückwärtskompatibel.

## Dashboard-MVP

Ein Admin erhält einen separaten Bereich **Access** im Manager:

1. Personen und Agenten auflisten, deaktivieren und neue Credentials anlegen.
2. Ein Profil einer Sandbox zuordnen oder die Sandbox im Profilformular ändern.
3. Pro Person/Agent genau die erlaubten Sandboxes und Aktionen setzen.
4. Eine verständliche Vorschau anzeigen: „Diese Identität kann diese Profile sehen / bedienen / automatisieren“.
5. Einen Agent-Key nur einmal anzeigen; danach nur Rotieren oder Deaktivieren erlauben.

Mobile bleibt dabei handhabbar: eine kompakte Person-/Agentenliste und ein eigener Bearbeitungsbildschirm statt überladener Tabellen im VNC-Splitscreen.

## Rollout ohne Aussperren

1. Datenbankmigration vorbereiten, aber Policy standardmäßig deaktiviert lassen.
2. Mit dem bestehenden `AUTH_TOKEN` als Bootstrap-Admin einen ersten lokalen Admin anlegen.
3. Profile in klar benannte Sandboxes einordnen; zunächst nur `view` für einen Testnutzer vergeben.
4. REST-, WebSocket- und CDP-Gates mit erlaubtem und nicht erlaubtem Credential ausführen.
5. Erst danach `ACCESS_CONTROL_ENABLED=1` in der privaten Deployment-Konfiguration setzen.
6. Einen Paperclip-Agenten mit einem einzelnen Test-Sandbox-Grant verbinden; keine Besitzer-Credentials in den Agenten einbauen.

Rollback bedeutet: Policy-Flag deaktivieren oder den Bootstrap-Admin nutzen. Das löscht keine Profile und keine Browserdaten.

## Akzeptanzkriterien

- Ein Viewer sieht nur die ihm zugewiesenen Profile und kann diese nicht steuern.
- Ein Operator kann nur seine Sandbox starten/bedienen und keine anderen Profile erraten oder über direkte URLs öffnen.
- Ein Paperclip-Agent kann nur CDP/VNC-Endpunkte seiner expliziten Sandbox verwenden.
- Ein unberechtigter CDP- oder VNC-WebSocket wird vor Upstream-Verbindung abgewiesen.
- API- und Browser-Tests prüfen erlaubte und verweigerte REST-, VNC- und CDP-Zugriffe.
- Key-Rotation macht den alten Agent-Key sofort unbrauchbar.
- Audit-Log enthält keine Secrets, Clipboard-Inhalte, CDP-Nutzdaten oder Passwörter.

## Offene Entscheidungen vor produktiver Aktivierung

- Welche konkrete Paperclip-Instanz und Agent-IDs sollen verbunden werden?
- Sollen Operatoren Profile nur starten/stoppen oder auch deren Fingerprint/Proxy-Einstellungen bearbeiten dürfen?
- Welche Sandbox-Namen sind für die vorhandenen Profile fachlich sinnvoll?
- Wie lange dürfen menschliche Sitzungen und Agent-Credentials maximal gültig sein?
- Welche Personen sollen die Admin-Rolle erhalten?

Diese Fragen ändern keine Grundsicherheitsgrenze. Die Implementierung kann mit sicheren Defaults starten: ein Bootstrap-Admin, keine Grants für neue Identitäten und keine CDP-Freigabe ohne explizites `automate`.
