# Wettbewerbs-UI- und Feature-Matrix

Stand: 22. Juli 2026

Diese Matrix bewertet offizielle Produktdokumentation und den aktuellen CloakBrowser-Manager-Fork. Sie kopiert keine fremden Marken, Screenshots oder Texte. Sie trennt belegte Fakten von Design-Inferenzen fuer die naechsten Produktentscheidungen.

## Kurzentscheidung

Der aktuelle Fork hat bereits eine starke Basis fuer den mobilen Live-Browser: `MobileSplitScreen`, `ProfileViewer`, serverseitige Task-Sessions, VNC/noVNC, Clipboard, Viewport-Steuerung, Tags, Proxy, Fingerprint-Felder, Launch-Args und Benchmark-Reports sind im Code vorhanden. Die naechsten Verbesserungen sollten nicht mehr Steuerleisten hinzufuegen, sondern vorhandene Flaechen besser organisieren: zuerst Browser/Composer/Fullscreen weiter verdichten, dann Profilorganisation, danach Proxy-/Fingerprint-Health und erst spaeter Live-Dev-Metriken sowie Extension-Sichtbarkeit.

## Rangierte Referenzprodukte

| Rang | Produkt | Belegte Staerke | Relevanz fuer CloakBrowser |
|---:|---|---|---|
| 1 | Browser Use Cloud | Chat-UI mit Live-Browser-Preview, Streaming-Messages, Follow-ups, Recording und serverseitigem API-Key-Vertrag; `liveUrl` kommt direkt bei Session-Erstellung zurueck. Quellen: [Chat UI](https://docs.browser-use.com/cloud/tutorials/chat-ui), [Live preview](https://docs.browser-use.com/cloud/browser/live-preview), [Profiles](https://docs.browser-use.com/cloud/guides/authentication) | Primaere UX-Referenz fuer Browser oben, Composer unten, Follow-up-Sessions und keine Client-Secrets. Keine Marken- oder Cloud-UI kopieren. |
| 2 | Browserbase + Stagehand | Interaktive Live View fuer Watch/Click/Type/Scroll, Embedding auch fuer Mobile, Session Inspector mit Netzwerk, Console, Performance und Recording; Stagehand kann Browserbase oder lokal nutzen und Browserbase bietet Stealth, Proxy und persistente Kontexte. Quellen: [Session live view](https://docs.browserbase.com/platform/browser/observability/session-live-view), [Using browser session](https://docs.browserbase.com/platform/browser/getting-started/using-browser-session), [Observability](https://docs.browserbase.com/platform/browser/observability/observability), [Stagehand browser config](https://docs.stagehand.dev/v2/configuration/browser) | Gute Referenz fuer Human-in-the-loop, Dev-Observability und Harness-Abstraktion. Die mobile Keyboard-Luecke ist explizit relevant: Browserbase dokumentiert, dass Mobile-Keyboards nicht nativ unterstuetzt sind und separat in Actions gemappt werden muessen. |
| 3 | Octo Browser | Profile lassen sich ueber Tags organisieren; Startseiten/Bookmarks und Proxy-Formate sind in Profileinstellungen vorgesehen; Produktseite nennt Gruppen/Tags, Fingerprint-Finetuning, Teamrechte, API-Automation und Proxy-Import/Export/Transfer. Quellen: [Profile settings](https://docs.octobrowser.net/en/profiles/browser-profile-settings/), [Functions](https://octobrowser.net/functions/) | Beste Referenz fuer kompakte Profil-Liste mit Tags, Farben und Team-/API-Denke. Fuer CloakBrowser besser als kleine Profil-Chips, nicht als grosse Kartenwand. |
| 4 | GoLogin | Fingerprint-Parameter werden pro Profil konfiguriert; WebRTC kann auf Proxy-IP ausgerichtet oder deaktiviert werden; jedes Profil trennt Fingerprint, Cookies und Proxy. Quellen: [Fingerprint settings](https://support.gologin.com/en/articles/14810056-profile-fingerprint-settings), [FAQ profiles](https://support.gologin.com/en/articles/14839273-faq-profiles) | Referenz fuer Proxy/Fingerprint-Konsistenz pro Profil und klare Health-Warnungen statt tiefer Expertenformulare im mobilen Hauptflow. |
| 5 | Multilogin | Fingerprint-Review umfasst WebRTC, Timezone, Geolocation, Screen, Fonts, Media, Canvas, WebGL, AudioContext und Port-Scan-Schutz; die Doku warnt vor zufaelligen, inkonsistenten Aenderungen. Quelle: [Fingerprint section](https://multilogin.com/help/en_US/profile-settings-fingerprint-section), [Fingerprint check](https://multilogin.com/help/en_US/how-to-check-browser-fingerprint) | Referenz fuer Health-Score: nicht beliebig viele Slider, sondern Konsistenzpruefung, erklaerte Warnungen und gezielte Korrekturen. |
| 6 | AdsPower | Profilanlage umfasst Proxy-Check, Gruppen, Tags, Fingerprint, Startseiten und automatische Extension-Kategorien; Proxy-Tags koennen farbig, in Bulk und fuer Random-Proxy-Auswahl genutzt werden. Quellen: [Create profile](https://help.adspower.com/docs/creating_browser_profiles), [Proxy tags](https://help.adspower.com/docs/Proxy-tag-Random-proxies) | Referenz fuer Profilorganisation, Proxy-Status und Extension-Sichtbarkeit. Konfiguration sollte bei CloakBrowser CLI-/Agent-getrieben bleiben; UI zeigt nur Status, Herkunft und Risiko. |
| 7 | MoreLogin | Bulk-Labels mit Farben, Gruppen, Bulk-Proxy-Detection, Bulk-Proxy-Modify und Bulk-Fingerprint-Parameter sind dokumentiert. Quelle: [Bulk operations](https://support.morelogin.com/en/articles/10204251-bulk-operations-for-profiles) | Referenz fuer spaetere Multi-Profile-Operationen. Jetzt nur die Datenstruktur so vorbereiten, dass Bulk spaeter nicht die mobile UI ueberlaedt. |
| 8 | Kameleo | Profil-Lifecycle per API: Fingerprint suchen, Profil erstellen, starten/stoppen, Proxy hinzufuegen, duplizieren, importieren/exportieren, loeschen; Best Practices: Proxy-Geo mit Locale/Timezone abgleichen, realistische Browser/Locale/Screen-Kombinationen nutzen. Quellen: [Manage profiles](https://developer.kameleo.io/tutorials/managing-profiles/), [Fingerprints](https://developer.kameleo.io/concepts/fingerprints/) | Beste Referenz fuer Agent-/CLI-first Profilbau und Anti-Stealth-Konsistenzlogik. |

## Feature-Matrix

| Bereich | Belegte Wettbewerbsfunktion | Aktueller Repo-Stand | Design-Inferenz fuer CloakBrowser |
|---|---|---|---|
| Kompakte Navigation | Browser Use zeigt Session-Flow mit Live-Preview, Streaming und Follow-ups; Browserbase bietet Live View als eingebettete Kontrollflaeche. Quellen: [Browser Use Chat UI](https://docs.browser-use.com/cloud/tutorials/chat-ui), [Browserbase Live View](https://docs.browserbase.com/platform/browser/observability/session-live-view) | `MobileSplitScreen.tsx` hat Full, Tools, Chat, Send, Grid, Viewport, Fullscreen-Fit, Phone fit und Harness-Actions; der aktuelle VCVM-Gate dokumentiert 318/318 Mobile-Checks mit 31 Screenshots. | **Jetzt:** vier dauerhafte Aktionen behalten, Tools/Chat weiter gegenseitig exklusiv halten, Viewport/Zoom im Fullscreen als Mini-Inspector statt grosses Sheet anzeigen. |
| Projekte, Ordner, Tags, Farben, Pins | Octo dokumentiert Tags pro Profil; AdsPower dokumentiert Gruppen und Tags; MoreLogin dokumentiert Labels mit Farben und Gruppen. Quellen: [Octo Profile settings](https://docs.octobrowser.net/en/profiles/browser-profile-settings/), [AdsPower Create profile](https://help.adspower.com/docs/creating_browser_profiles), [MoreLogin Bulk operations](https://support.morelogin.com/en/articles/10204251-bulk-operations-for-profiles) | `Profile` enthaelt `sandbox_id`, `tags: { tag, color }[]`, `notes`, `color_scheme`; echte Folder/Project/Pin-Felder sind nicht belegt. | **Next:** vorhandene Tags/Farben sichtbar machen, `sandbox_id` als Projekt-/Workspace-Gruppierung verwenden, Pins als UI-Prferenz pro Profil einfuehren. Ordner erst als einklappbare Gruppierung, nicht als neues Sicherheitsmodell. |
| Proxy- und Fingerprint-Health | GoLogin, Multilogin und Kameleo betonen konsistente Fingerprint-/Proxy-/Timezone-/Locale-Signale; MoreLogin und AdsPower zeigen Proxy-Detection bzw. Proxy-Check. Quellen: [GoLogin fingerprint](https://support.gologin.com/en/articles/14810056-profile-fingerprint-settings), [Multilogin fingerprint check](https://multilogin.com/help/en_US/how-to-check-browser-fingerprint), [Kameleo fingerprints](https://developer.kameleo.io/concepts/fingerprints/), [MoreLogin Bulk detection](https://support.morelogin.com/en/articles/10204251-bulk-operations-for-profiles) | Backend normalisiert und validiert Proxy-Strings; Profile haben `proxy`, `timezone`, `locale`, `platform`, `user_agent`, `screen_width`, `screen_height`, GPU und Hardware-Felder. Automatischer Health-Score ist nicht als Produktflaeche belegt. | **Then:** Health als Ampel/Score pro Profil: Proxy erreichbar, IP/Geo/Timezone/Locale plausibel, WebRTC-Leak-Risiko, Screen/UA/Platform stimmig. Keine sensiblen Proxy-Secrets in UI, Logs oder Reports. |
| Live-Session- und Dev-Metriken | Browserbase Session Inspector zeigt Live-Browser-Zustand, Netzwerk, Console, Performance, Resource Usage, Recording/Replay; Observability listet CDP-Events, Netzwerklogs, Token usage und Execution time fuer Stagehand. Quellen: [Using browser session](https://docs.browserbase.com/platform/browser/getting-started/using-browser-session), [Observability](https://docs.browserbase.com/platform/browser/observability/observability) | `BenchmarkReport`-Typen und `REMOTE-STREAMING-BENCHMARK.md` existieren; mobile UI soll Benchmarks laut Audit nicht dauerhaft anzeigen. | **Later:** Dev-Panel nur fuer Admin/Debug: VNC open, erstes Frame, Canvas-Change/s, reconnects, WebSocket status, optional CDP/network summaries. Nicht in die Standard-Mobile-UI legen. |
| Extensions | AdsPower dokumentiert automatische Extension-Kategorien pro Profil; Browserbase Observability fuehrt eine Extension-ID als Session-Property. Quellen: [AdsPower Create profile](https://help.adspower.com/docs/creating_browser_profiles), [Browserbase Observability](https://docs.browserbase.com/platform/browser/observability/observability) | `launch_args` ist im Profilmodell vorhanden und Tests decken `--load-extension=/tmp/ext` ab; eine sichere Extension-Inventaransicht mit Icon/Quelle ist nicht belegt. | **Later:** CLI installiert und verwaltet Extensions; UI zeigt read-only Inventar: Name, ID, Version, Quelle, Hash/Path, aktiv/inaktiv, Risiko. Keine manuelle Extension-Installation in Mobile. |
| Mobile/Fullscreen | Browserbase Live View kann in Apps eingebettet werden und nennt Mobile Viewports, aber Mobile-Keyboards muessen separat gemappt werden. Browser Use Live Preview empfiehlt responsive iframe-sizing statt fixer Groesse. Quellen: [Browserbase Session live view](https://docs.browserbase.com/platform/browser/observability/session-live-view), [Browser Use Live preview](https://docs.browser-use.com/cloud/browser/live-preview) | `MobileSplitScreen.tsx` nutzt `visualViewport`, Fullscreen-Fit-Modi, Viewport-Presets und denselben Viewer-Knoten; `ProfileViewer.tsx` kapselt noVNC, reconnect, Clipboard und Scale. | **Jetzt:** Fullscreen als echte Arbeitsflaeche behandeln: Viewport, Zoom, Fit-Modus, Paste und Chat-Collapse bleiben erreichbar, aber als sehr kleine Overlays. Softwaretastatur darf den Composer nicht verdecken. |

## Umsetzung fuer diesen Repo-Fork

### Now: kompakter Browser, Composer und Fullscreen-Sessions

Belegter Stand: `MobileSplitScreen.tsx` und `ProfileViewer.tsx` besitzen bereits kompakte Controls, Fullscreen-Fit, Phone fit, Viewport-Editor, Grid, Clipboard/Paste und Harness-Actions. Der aktuelle VCVM-Gate dokumentiert 318/318 bestandene Checks mit 31 Screenshots ueber fuenf Viewports und das Access-Dashboard.

Umsetzung:

- Dauerhaft sichtbar bleiben nur Browser, Composer und maximal vier Dock-Aktionen.
- Tools werden ein Sheet mit Sektionen, nicht mehrere parallele Panels.
- Fullscreen bekommt eine Mini-Leiste: Fit, Zoom, Viewport, Paste, Exit.
- Tastaturzustand nutzt weiter `visualViewport`; alle nicht notwendigen Dock-Buttons verschwinden waehrend Text-Eingabe.
- Quick-Actions bleiben typed actions, keine URL-Bookmarks.

### Next: Profilorganisation

Belegter Stand: `sandbox_id`, farbige `tags`, `notes` und Access-Grants existieren. Wettbewerber nutzen Tags, Labels, Gruppen und Farben zur Profilnavigation.

Umsetzung:

- Links/oben im Session-Sheet: Projektgruppe aus `sandbox_id`.
- Innerhalb einer Gruppe: kompakte Profilzeile mit Status, Farbe, Tags, Aufloesung, Health-Platzhalter.
- Pin nur als lokale UI-Prioritaet: gepinnte Profile stehen oben; Sicherheitsrechte bleiben unveraendert ueber Grants.
- Ordner als reine Gruppierung in der UI einfuehren, spaeter persistieren.

### Then: Proxy- und Fingerprint-Health

Belegter Stand: Proxy/Fingerprint-Felder sind im Modell vorhanden; Wettbewerber pruefen Proxy-Konnektivitaet und warnen vor inkonsistenten Signalen.

Umsetzung:

- Serverjob `profile_health`: Proxy-Reachability, IP-Land, Timezone/Locale-Abgleich, WebRTC-Leak-Test, Screen/UA/Platform-Konsistenz.
- UI zeigt nur Score, letzte Pruefung und wichtigste Warnung.
- Detailansicht erklaert "belegt" versus "Inferenz": zum Beispiel Proxy unreachable ist belegt, Risk wegen Timezone/IP-Mismatch ist Inferenz.
- Kein automatisches Rotieren ohne expliziten Agent-/CLI-Auftrag.

### Later: Live-Dev-Metriken und Extension-Sichtbarkeit

Belegter Stand: Benchmark-Runner und Reports existieren; `launch_args` kann Extensions starten. Wettbewerber zeigen Session-/Dev-Observability und Extension-/Proxy-Status.

Umsetzung:

- Admin-only Dev-Panel: aktuelle VNC/WebSocket-Verbindung, reconnect count, first-frame, Canvas-Change/s, CDP-Events, Network/Console-Ausschnitte.
- Extension-Inventar aus CLI/Browser-Inspection generieren, nicht per Mobile-Formular pflegen.
- Read-only Extension-Karte: Icon, Name, Version, ID, Quelle, Installationspfad/Hash, Profilzuordnung.
- Metriken bleiben Debug-Information, nicht Teil des normalen Mobile-Flows.

## Fakt oder Inferenz

| Aussage | Status |
|---|---|
| Browser Use dokumentiert Chat-UI, Live-Preview, Follow-ups, Recording und serverseitige SDK-Nutzung. | Verifizierter Fakt aus offizieller Doku. |
| Browserbase dokumentiert interaktive Live View, Mobile-Embedding, Session Inspector und Performance-/Netzwerk-/Console-Observability. | Verifizierter Fakt aus offizieller Doku. |
| Octo, AdsPower, MoreLogin nutzen Tags/Labels/Gruppen/Farben fuer Profil- oder Proxy-Organisation. | Verifizierter Fakt aus offizieller Doku. |
| GoLogin, Multilogin und Kameleo stellen Proxy/Fingerprint/Locale/Timezone-Konsistenz als wichtig dar. | Verifizierter Fakt aus offizieller Doku. |
| CloakBrowser sollte keine grossen neuen Mobile-Panels bekommen, sondern vorhandene Tools verdichten. | Design-Inferenz aus Ziel, Repo-Stand und Referenzen. |
| `sandbox_id` kann kurzfristig als Projekt-/Gruppenachse dienen. | Design-Inferenz; technisch naheliegend, aber noch kein vollstaendiges Projektmodell. |
| Fingerprint-Health sollte als Score statt als Expertenformular erscheinen. | Design-Inferenz aus Wettbewerbermustern und Mobile-UX-Ziel. |
| Extension-Verwaltung sollte CLI-/Agent-first bleiben und im UI nur sichtbar sein. | Design-Inferenz aus Nutzerziel und vorhandenem `launch_args`-Modell. |

## Nicht uebernehmen

- Keine fremden Logos, Farben, Screenshots, Texte oder Markenbegriffe in der UI.
- Keine echten Bookmarks als Hauptnavigation. Wenn URLs/Startseiten spaeter gebraucht werden, bleiben sie Profilkonfiguration oder typed harness action.
- Keine Live-Benchmarks in der normalen Mobile-Oberflaeche.
- Keine Proxy-, API-, Browser- oder Admin-Secrets in Chat, Screenshots, Logs oder Markdown-Reports.
- Keine beliebigen Fingerprint-Slider fuer Mobile; Health und gezielte Korrektur sind wichtiger als Formularbreite.
