# Remote-Streaming-Benchmark und Stack-Empfehlung

Stand: 21. Juli 2026

Dieser Bericht trennt Messwerte, Funktionsnachweise und Architekturentscheidungen bewusst voneinander. Die Zahlen stammen aus lokalen Läufen auf derselben Entwicklungsmaschine, aber nicht aus einem vollständig kontrollierten Laboraufbau. Sie sind eine belastbare Richtungsentscheidung für das Mobile-MVP, kein allgemeines Produktversprechen.

## Kurzentscheidung

Für den aktuellen CloakBrowser Manager bleibt **KasmVNC 1.3.3 + noVNC 1.4.x bei 1024 × 576** die empfohlene Produktionsbasis.

- Der vorhandene Stack ist der einzige Kandidat, dessen vollständige Kette `Browser → X11 → KasmVNC → FastAPI-WebSocket-Proxy → noVNC → echter Canvas` lokal end-to-end nachgewiesen wurde.
- 1024 × 576 benötigt im warmen Vergleich deutlich weniger Startzeit und RAM als 1440 × 900.
- Der isolierte A/B-Lauf bestätigt keinen Geschwindigkeitsgewinn durch KasmVNC 1.4; 1.3.3 bleibt deshalb die risikoärmere Produktionsbasis.
- Selkies liefert im isolierten WebSocket-POC einen echten CloakBrowser-Stream samt Eingabe. Der rohe Client ist aber auf einem iPhone-Viewport sichtbar letterboxed und nicht in den Produkt- und Profil-Lifecycle integriert.
- Sunshine/Moonlight passt eher zu einem nativen Companion-Client als zu einem eingebetteten iPhone-Web-Viewer.
- Apache Guacamole ist ein clientloses Remote-Desktop-Gateway, aber kein Drop-in-Ersatz: Es benötigt eine eigene Web-App- plus `guacd`-Ebene und passt nicht direkt auf den bestehenden WebSocket-only-KasmVNC-Pfad.
- noVNC 1.7 ist kein Drop-in-Update für den aktuellen Viewer.

## Vergleichsmatrix

| Kandidat | Gemessener/ermittelter Zustand | Evidenzgrad | Entscheidung |
|---|---|---|---|
| KasmVNC 1.3.3 + noVNC 1.4.x | Vollständiger echter Browserstream und Mobile-Vollbild lokal geprüft | E2E-verifiziert | Beibehalten |
| KasmVNC 1.4 | Vollständiger isolierter Browser-, Mobile- und RFB/CDP-E2E-Lauf gegen dieselbe App-Konfiguration bestanden; kein Performancegewinn gegenüber 1.3.3 | E2E-verifiziert, lokale Stichprobe | Nicht migrieren |
| noVNC 1.7 | Build scheitert ohne Migration an ESM-/Export-Änderungen und entfernter `showDotCursor`-API | Kompatibilitätsprüfung | Nicht direkt aktualisieren |
| Selkies | Echter CloakBrowser auf X11, JPEG- und H.264-Frame im Browser sowie Remote-Eingabe lokal nachgewiesen; keine Produktintegration und kein brauchbares mobiles Raw-UI | Isolierter Browser-E2E-POC | Nicht als Drop-in ersetzen |
| Sunshine + Moonlight | Architektur und Clientmodell geprüft; kein passender allgemeiner Web-Embed-Pfad | Architekturprüfung | Kein Web-MVP-Core |
| Apache Guacamole | Browser-/Touch-Unterstützung und Gateway-Architektur geprüft; bestehender KasmVNC-Server hat bewusst keinen Raw-VNC-TCP-Port | Architektur-/Integrationsprüfung | Kein Web-MVP-Core |
| Browser-Use Chat UI | Interaktions- und Informationsarchitektur geprüft | UI-Referenz | Als UX-Referenz, nicht als Streamingstack |

## Upstream-Snapshot

Die folgende Einordnung hält den Stand der verglichenen Projekte am 20. Juli 2026 fest. Sie ist bewusst knapp und ergänzt die Messwerte oben um den aktuellen Funktionskontext der jeweiligen Upstreams:

- **KasmVNC 1.4.0**: Die offizielle Doku sagt explizit, dass die Dokumentationsversion zur installierten Version passen soll, und beschreibt KasmVNC als modernen, browserbasierten Streaming-Stack mit Fokus auf Sicherheit und einfacher Bereitstellung.
- **noVNC**: Das Upstream-README beschreibt noVNC als HTML-VNC-Client und App mit Unterstützung für moderne Browser inklusive mobiler Browser, Skalierung, Touch-Gesten und Clipboard.
- **Selkies**: Die Projektseite beschreibt Selkies als HTML5-Remote-Desktop mit WebSockets als Standardtransport, optionalem WebRTC und einem Performance-Ziel von mindestens 60 fps bei Full-HD.
- **Sunshine**: Das Upstream-README positioniert Sunshine als Game-Stream-Host mit Web-UI für Konfiguration und Pairing aus Browser oder mobilem Gerät.
- **Moonlight Qt**: Das Upstream-README nennt explizit mobile Clients für Android und iOS, also ein starkes Signal für den nativen-Client-Pfad statt eines eingebetteten Web-Views.
- **Apache Guacamole 1.6.0**: Die offizielle Architektur trennt Web-App und `guacd`; `guacd` übersetzt erst vom Guacamole-Protokoll zu VNC, RDP oder SSH. Die Nutzungsdokumentation beschreibt Touch-Emulation, Bildschirm-Skalierung und Bildschirmtastatur für mobile Geräte.
- **Browser Use Chat UI**: Die offizielle Tutorial-Seite beschreibt den Session-Flow mit Live-Browser-Preview, Streaming-Messages, Follow-ups und Recording-Download. Genau diese Interaktionsidee ist die UX-Referenz für unseren mobilen Composer, nicht die Streaming-Schicht selbst.

Diese Snapshot-Einordnung ändert die Produktentscheidung nicht: Für den aktuellen CloakBrowser-Manager bleibt KasmVNC 1.3.3 + noVNC 1.4.x die am besten nachgewiesene Web-Basis, während Browser Use nur als Chat-/Session-UX-Referenz dient.

## KasmVNC/noVNC: lokale Messungen

| Lauf | Browserstart | WebSocket-Verbindung | Erster Frame | Frame-Payload | RAM |
|---|---:|---:|---:|---:|---:|
| 1024 × 576, kalt | 16,347 s | 3.937 ms | 4.201,9 ms | 2.359.456 Byte | 322,2 MiB |
| 1024 × 576, warm | 3.080,9 ms | 52,0 ms | 136,2 ms | 2.359.456 Byte | 329,1 MiB |
| 1440 × 900, warm | 6.828,9 ms | 40,4 ms | 169,8 ms | 5.184.280 Byte | 982,6 MiB |

Der kalte 1024-×-576-Lauf enthält einen Browserdownload und ist daher nicht direkt mit den warmen Läufen vergleichbar. Der aussagekräftigste Vergleich ist warm gegen warm:

- Die Launchdauer bei 1024 × 576 lag rund 55 % unter 1440 × 900.
- Der gemessene RAM-Wert lag rund 67 % niedriger.
- Der Frame-Payload lag rund 54 % niedriger.
- Die WebSocket-Verbindungszeit war in beiden warmen Läufen klein und ist hier nicht der dominante Faktor.

Diese Relationen sind eine Inferenz aus den lokalen Messwerten. Für eine belastbare Release-Regression müssen mindestens fünf identische Wiederholungen pro Konfiguration mit Median und p95 folgen.

### Frischer Browser-Adapter-Nachtest (fünf warme Läufe)

Ein zusätzlicher, frischer Runner-Lauf vom 20. Juli 2026 (UTC) prüfte den bereits verbundenen lokalen Managerbrowser fünfmal. Er misst nur die beobachtbaren TCP-/HTTP- beziehungsweise WebSocket-Upgrade-Meilensteine und trennt sie bewusst von Startzeit, erstem Bild, Eingabeantwort und Internet-Latenz. Die Werte sind daher eine lokale Regressionsbasis für den bestehenden Pfad — **kein** fairer Geschwindigkeitsvergleich mit nicht eingerichteten oder architektonisch abweichenden Stacks.

| Kandidat | Läufe | Ergebnis | Median | p95 | Einordnung |
|---|---:|---|---:|---:|---|
| KasmVNC Manager health | 5/5 | erreichbar | HTTP First Byte 8,196 ms | 182,855 ms | lokaler Control-Plane-Smoketest; ein Ausreißer ist im p95 sichtbar |
| KasmVNC/noVNC live WebSocket | 5/5 | Upgrade erreichbar | WebSocket-Handshake 66,627 ms | 68,595 ms | derselbe echte Browser-Stream-Pfad, warm und lokal |
| Selkies WebSocket POC | – | `not_installed` | – | – | GStreamer/Selkies war auf diesem Host nicht installiert; keine erfundene Zeit |
| Sunshine + Moonlight | – | `architecture_only` | – | – | nativer Clientpfad, kein eingebetteter Browser-WebSocket konfiguriert |
| Apache Guacamole | – | `architecture_only` | – | – | Gateway-Pfad, in diesem Lauf nicht provisioniert |

Der daraus erzeugte, serverseitig redigierte Report trennte die zwei gemessenen Kandidaten von drei nicht gemessenen Kandidaten. Lokale Endpunkte, Profilkennungen und Header erscheinen weder im öffentlichen Report noch in dieser Dokumentation.

### Frischer isolierter Preview-Nachtest (21. Juli 2026)

Ein weiterer fünfmaliger Warm-Nachtest gegen die isolierte, bereits verbundene Mobile-Vorschau bestätigte den lokalen KasmVNC/noVNC-Pfad: Der Health-Endpunkt erreichte einen Median First Byte von **2,233 ms** (p95 15,891 ms), der echte VNC-WebSocket-Upgrade einen Median Handshake von **4,541 ms** (p95 17,442 ms). Wie im vorherigen Lauf blieben Selkies `not_installed` sowie Sunshine/Moonlight und Guacamole `architecture_only`; sie erhalten keine erfundenen Vergleichswerte. Der redigierte, README-verlinkte Einzelreport steht in [streaming-benchmark-latest.md](streaming-benchmark-latest.md). Der Nachtest misst nur den warmen Loopback-Pfad und ist ausdrücklich kein Vergleich mit dem älteren Container, Startzeiten oder anderen Technologien.

### Finaler r49-Warm-Nachtest (20 Läufe)

Der finale r49-Preview verwendet einen persistenten Workspace-Mount statt eines temporären Datenpfads. Gegen seine bereits laufende 1024-×-576-Session bestanden Health und VNC-WebSocket jeweils **20/20** lokale Läufe.

| Kandidat | Median | p95 | Maximum | Aussagegrenze |
|---|---:|---:|---:|---|
| Manager HTTP First Byte | 1,438 ms | 4,005 ms | 14,794 ms | Control Plane, kein Frame |
| KasmVNC/noVNC WebSocket-Upgrade | 3,457 ms | 8,931 ms | 49,980 ms | Upgrade, kein Touch-to-Pixel |

Der vollständige mobile Browser-Gate desselben Builds bestand anschließend **249/249 Checks** über fünf Viewpoints und erzeugte 22 Screenshot-Artefakte. Vier Authentifizierungswege – Legacy-Token, Bootstrap-Token, benannter Operator und benannter Viewer – erreichten einen verbundenen Canvas; Viewer-Eingabe blieb server- und UI-seitig gesperrt. Die Details und zehn priorisierten Eingabeverbesserungen stehen im [kritischen Mobile-/Auth-/Latenz-Audit](MOBILE-STREAMING-AUTH-LATENCY-AUDIT-2026-07-21.md).

### r50 VCVM/Neko über Tailscale

Am 21. Juli 2026 wurde zusätzlich ein bereits laufender Neko/Chrome-Stack auf der VCVM über Tailscale geprüft. Dieser Lauf ist ein Transport- und Login-Beleg, kein fairer Ersatzbenchmark gegen den CloakBrowser-KasmVNC-Pfad.

| Messpunkt | Ergebnis |
|---|---:|
| Tailscale-Pfad | DERP(nue), keine direkte Verbindung |
| Tailnet-Ping | p50 **59 ms**, Bereich **45-153 ms** |
| VCVM-lokaler HTTP-Zugriff | p50 TTFB **1,318 ms**, p50 total **1,372 ms** |
| Mac zu VCVM per SSH-Tunnel | p50 TTFB **226,095 ms**, p50 total **230,826 ms** |
| Browser First Paint | **2.140 ms** |
| Browser FCP | **2.232 ms** |
| Browser Load | **2.953,6 ms** |

Codex Computer Use absolvierte den geschützten Login und beobachtete `/ws`. WebRTC ICE blieb aber bei `checking` und wechselte danach zu `failed`; das Video blieb bei `readyState 0`. Deshalb wurde **kein FPS-Wert** berichtet. Die nächste sinnvolle Performance-Arbeit ist direkte Tailnet-Konnektivität beziehungsweise UDP/ICE zu reparieren und erst danach Frame- und Touch-to-Pixel-Messungen zu wiederholen.

## KasmVNC 1.3.3 gegen 1.4.0: isolierter A/B-Lauf

Am 20. Juli 2026 wurden beide KasmVNC-Versionen in getrennten lokalen Containern mit identischem 1024-×-576-Profil, deaktiviertem Clipboard-Sync, derselben aktuellen Frontend-Ausgabe und demselben aktuellen `browser_manager.py` geprüft. Die App war jeweils nur auf `127.0.0.1` gebunden; der normale Produktionscontainer blieb unverändert. Für KasmVNC 1.4 war das aktuelle Backend notwendig: Das zuvor gebaute Image enthielt noch den bereits behobenen Fehler, `search_engine` als nicht unterstütztes Chromium-Launch-Argument weiterzugeben.

| Metrik | KasmVNC 1.3.3 | KasmVNC 1.4.0 | Einordnung |
|---|---:|---:|---|
| Warmer API-Launch, 5 Läufe, Median | 831,6 ms | 833,2 ms | praktisch gleich |
| Warmer API-Launch, 5 Läufe, konservatives p95 (Maximum der fünf Werte) | 919,8 ms | 1.750,5 ms | 1.4 mit einem deutlichen Ausreißer |
| Erster Mobile-UI-zu-`Connected`-Durchlauf nach Stopp | 1.381,3 ms | 3.145,7 ms | Einzelmessung; 1.3 war schneller |
| Stabiles Container-RAM mit verbundenem Browser | 312,0 MiB | 312,4 MiB | kein relevanter Unterschied |
| Vier Mobile-Viewports, echter Canvas, RFB-Keyboard und CDP-Probe | 88/88 Checks, 13 Screenshots | 88/88 Checks, 13 Screenshots | beide bestanden |

Die fünf warmen Launchwerte lauten für 1.3.3 `919,8 / 834,6 / 810,5 / 831,6 / 820,9 ms` und für 1.4.0 `1.750,5 / 922,3 / 807,3 / 830,4 / 833,2 ms`. Die erste Live-Verbindung ist je Version nur einmal aus einem gestoppten Profil gemessen und daher kein statistischer Beweis. Zusammen mit dem fehlenden Medianvorteil und dem 1.4-Ausreißer reicht die Evidenz aber klar gegen ein Upgrade im Mobile-MVP.

## Selkies: isolierter Browserstream-POC

Das arm64-Image war ungefähr 1,245 GB groß. Der erste korrigierte Build benötigte ungefähr 85 Sekunden; danach waren die relevanten Layer gecacht.

| Modus | Zeit bis Server bereit | Idle-CPU | RAM |
|---|---:|---:|---:|
| H.264 | 7.278 ms | ca. 1–2,3 % | ca. 73–75 MiB |
| JPEG | 2.141 ms | ca. 1–2,3 % | ca. 73–75 MiB |
| WebRTC | 1.569 ms | ca. 1–2,3 % | ca. 73–75 MiB |

Der frühere Server-Smoke-Test wurde danach zu einem echten, aber bewusst isolierten Browserstream erweitert. Dafür wurde der echte, aktuell verwendete CloakBrowser-Chromium auf einem separaten X11-Display gestartet und über den Selkies-WebSocket-Client betrachtet. Das Image enthielt die gebauten Web-Assets nicht; der Client musste deshalb aus dem vorhandenen Selkies-Web-Quellbaum gebaut werden. Dieser Aufbau ist ein lokales Testartefakt, keine vorgeschlagene Produktionsinstallation.

| Modus | Frische Clients bis erster nichtschwarzer Frame | Median | Remote-Bild und Eingabe | Momentaufnahme nach Messung |
|---|---|---:|---|---|
| JPEG | 1.482,2 / 1.182,2 / 981,7 ms | 1.182,2 ms | echter CloakBrowser sichtbar; Tastatureingabe im Browser nachgewiesen | 1,21 % CPU, 195,9 MiB Container-RAM |
| H.264 | 2.317,9 / 1.634,4 / 1.686,5 ms | 1.686,5 ms | echter CloakBrowser sichtbar; Klick auf den Stream, `Control+L`, danach `h` in der Remote-Adressleiste sichtbar | 19,49 % CPU, 406,7 MiB Container-RAM |

Die Messung startet jeweils einen **frischen Browserclient gegen einen bereits laufenden Selkies-Server** bei 1280 × 576. Sie ist deshalb nur innerhalb dieses Selkies-POCs vergleichbar, nicht mit den KasmVNC-API-Launch- oder Profilstartzeiten. Die RAM-/CPU-Werte sind einzelne, nachgelagerte Momentaufnahmen und kein statistischer Vergleich. In dieser CPU-Umgebung fand Selkies keine GPU; H.264 fiel auf CPU-Encoding zurück. JPEG war im untersuchten Pfad schneller und leichter.

Die H.264-Ausgabe wurde zusätzlich mit einer iPhone-12-Browseremulation geöffnet. Ein nichtschwarzer Frame kam an, die Rohoberfläche blieb jedoch stark letterboxed und behielt das 1280-×-576-Desktopbild. Das ist kein akzeptabler mobiler Produktzustand. Die Emulation verwendet Chromium mit iPhone-User-Agent und ersetzt weder Mobile Safari/WebKit noch ein physisches iPhone.

Damit ist Selkies als Techniknachweis weiter als der ursprüngliche Server-Smoke, aber kein direkter Ersatz für den bestehenden KasmVNC/noVNC- und FastAPI-Profilpfad: Es fehlen die Produktintegration, das mobile Shell-/Grid-/Composer-Verhalten, eine authentifizierte Tailscale-HTTPS-Abnahme, Mobile-Safari-Interaktionen und ein vergleichbarer WebRTC-E2E-Lauf.

## Sunshine/Moonlight

Sunshine ist auf macOS weiterhin ein experimenteller Hostpfad; Moonlight ist primär ein nativer Client. Das Modell eignet sich für sehr flüssige Fernsteuerung mit einem installierten Companion-Client, nicht für den geforderten eingebetteten VNC-/Web-Workspace in Mobile Safari. Ein allgemeiner offizieller Webclient, der den aktuellen noVNC-Embed ohne Produktumbau ersetzt, wurde nicht nachgewiesen.

Deshalb ist der Stack für dieses Produkt derzeit ein **Conditional No-Go**: nur erneut prüfen, wenn ein nativer iOS-Companion ausdrücklich Teil des Produkts wird.

## Apache Guacamole

Apache Guacamole ist ein browserbasiertes Remote-Desktop-Gateway für VNC, RDP und SSH. Seine offizielle Architektur trennt die Web-Anwendung von `guacd`, das den Guacamole-Datenstrom in das jeweilige Remote-Desktop-Protokoll übersetzt. Die Touch-Dokumentation deckt Touch-Emulation, Skalierung und Bildschirmtastatur ab. Damit ist Guacamole für Mobile Safari grundsätzlich verwendbar, liefert aber keinen belegten UX- oder Leistungs-Vorteil gegenüber dem bereits mobilfähigen noVNC-Pfad.

Für den aktuellen Manager wäre Guacamole keine direkte Migration: Der bestehende KasmVNC-Start konfiguriert `-websocketPort` und deaktiviert den Raw-VNC-TCP-Port mit `-rfbport -1`; außerdem kapseln FastAPI-Profilverwaltung, berechtigter VNC-WebSocket-Proxy und CDP-Automation jeweils produkt-spezifische Verantwortlichkeiten. Eine Guacamole-Einführung bräuchte deshalb mindestens einen VNC-Bridge-/Raw-Port-Pfad sowie ein separates Verbindungs- und Berechtigungsmodell. Die zusätzliche Kette `Browser → Guacamole-Web-App → guacd → VNC` ist eine Architektur-Inferenz aus der offiziellen Aufteilung, keine gemessene Latenzbehauptung.

Ein lokaler Vergleich wäre als enger Transporttest gegen denselben Browser, dieselbe Auflösung und identische Touch-/Keyboard-Proben möglich. Er wäre jedoch kein fairer Drop-in-Produktbenchmark, solange die zusätzliche Guacamole-Authentifizierung, das Provisioning und die bestehende CDP-/Profilsteuerung nicht gleichwertig integriert sind. Deshalb bleibt Guacamole ein **Conditional No-Go**: nur erneut messen, falls ein eigenständiges Gateway-Produktziel die zusätzliche Infrastruktur rechtfertigt.

## noVNC 1.7 und KasmVNC 1.4

Der direkte noVNC-1.7-Versuch traf auf geänderte ESM-Exports und die entfernte `showDotCursor`-Oberfläche. Eine Aktualisierung benötigt daher eine kleine Viewer-Migration und einen eigenen Reconnect-, Touch-, Fullscreen- und Safari-Test. Sie darf nicht gemeinsam mit einem KasmVNC-Upgrade als untrennbares Paket bewertet werden.

Das KasmVNC-1.4-Image ließ sich bauen. Sein erster Lauf war durch einen inzwischen behobenen `search_engine`-Drift im alten Image blockiert; der vollständige Nachtest mit dem aktuellen Backend ist oben dokumentiert. Die Version ist funktional kompatibel, bringt in dieser Stichprobe jedoch keine messbare Mobile-Performance-Verbesserung.

## Reproduzierbares Messprotokoll für den nächsten A/B-Lauf

1. Identisches persistentes Testprofil mit 1024 × 576 und deaktiviertem Clipboard-Sync verwenden.
2. Browser- und Imagecache vor einem kalten Lauf dokumentieren; warme Läufe getrennt ausweisen.
3. Pro Kandidat mindestens fünf warme Wiederholungen durchführen.
4. Messen: API-Launch bis `running`, WebSocket-Upgrade, erster nichtleerer Frame, Zeit bis Eingabeantwort, CPU, RSS/RAM und übertragene Bytes.
5. Danach dieselbe Sequenz bei 1440 × 900 wiederholen.
6. Auf iPhone Safari zusätzlich Reconnect nach App-Hintergrund, Touch-Drag, Keyboard, Vollbild-Fallback und Tailscale-HTTPS prüfen.
7. Median, p95 und Ausreißer zusammen berichten; fehlgeschlagene Läufe nicht aus der Stichprobe entfernen.

Der reproduzierbare Adapter für neue Transport- und Speed-Tests liegt unter `scripts/streaming_benchmark_runner.py`; Details stehen in [STREAMING-SPEED-TEST-RUNNER.md](STREAMING-SPEED-TEST-RUNNER.md). Er liest eine JSON-Kandidatenliste, sendet JSONL-Ereignisse an stdout und schreibt danach einen JSON- und Markdown-Report. Beispiel:

```bash
python3 scripts/streaming_benchmark_runner.py \
  --config scripts/streaming_benchmark_example.json \
  --output-dir artifacts/streaming-benchmark/$(date -u +%Y%m%dT%H%M%SZ) \
  --iterations 5 \
  --latest-json "${BENCHMARK_REPORT_PATH:-/data/benchmark-report.json}" \
  --latest-markdown docs/streaming-benchmark-latest.md
```

Der Runner unterscheidet absichtlich zwischen `measured`, `not_installed` und `architecture_only`. Eine fehlende lokale Installation oder ein reiner Architekturpfad erhält keine erfundenen Zeiten; ein erreichbarer HTTP-/WebSocket-/Command-Kandidat erhält dagegen rohe Messungen und Median-Min-Max-P95-Zusammenfassungen. Der Report ist als Headless-/Offline-Diagnostik gedacht; r50 zeigt Benchmarks nicht in der mobilen UI. Die öffentliche Projektion enthält bewusst keine lokalen Pfade, Endpunkte, Commands, Header oder Prozessausgaben.

Der Browser-/UI-Gate-Runner liegt unter `scripts/mobile_ui_gate.py`. Er prüft fünf Viewports (iPhone 14, iPhone SE, iPhone Pro Max, iPhone 14 Landscape und Touch-Tablet), Touch-Ziele, Overflow, Split-Geometrie, den injizierten Codex-Computer-Use-Composer, Grid, Fullscreen-Fokus und – mit einer Profil-ID – genau einen echten VNC-Canvas. Der finale r50-Lauf bestand **272/272 Checks** und erzeugte **22 Screenshots**. Mit Profil-ID öffnet der Runner außerdem den manuellen iOS-Paste-Fallback, prüft dessen Touch-Ziele und bestätigt den kontrollierten Clipboard-Bridge-Rundlauf, ohne Clipboard-Text im Report zu speichern. Optional tippt `--remote-probe-url` eine harmlose, eindeutige URL per Keyboard-Events durch noVNC/RFB und verifiziert die Zielseite danach über den CDP-Proxy. Seine Screenshotprüfung validiert Abmessungen und Mindestdateigröße; die abschließende semantische Sichtprüfung bleibt bewusst ein separater menschlicher oder Vision-Agent-Gate.

## Gepinnte Referenzstände

Die folgenden `HEAD`-Stände wurden am 20. Juli 2026 direkt aus den öffentlichen Repositories gelesen:

- [KasmVNC @ `f77a5ca`](https://github.com/kasmtech/KasmVNC/tree/f77a5ca3e82f16709d93ae6b016d5baa0168208e)
- [noVNC @ `7c36fab`](https://github.com/novnc/noVNC/tree/7c36fabe599e053c5a81e98e091ac636f6c1e174)
- [Selkies GStreamer @ `44fb739`](https://github.com/selkies-project/selkies-gstreamer/tree/44fb7391901757605e0e617875fdfd4c9dda5906)
- [Sunshine @ `9d2409f`](https://github.com/LizardByte/Sunshine/tree/9d2409f71b60f1812f482e6dd807dc52e2f72fe7)
- [Moonlight Qt @ `2328713`](https://github.com/moonlight-stream/moonlight-qt/tree/2328713f4e7b8442e6bd49238b4eba27031a4d9f)
- [Apache Guacamole: Architektur](https://guacamole.apache.org/doc/gug/guacamole-architecture.html), [Mobile-/Touch-Bedienung](https://guacamole.apache.org/doc/gug/using-guacamole.html) und [Release 1.6.0](https://guacamole.apache.org/releases/1.6.0/)
- [Browser-Use Chat UI @ `0a0e855`](https://github.com/browser-use/chat-ui-example/tree/0a0e855205bd90fb782c60e7dabe8149b8476acf)

## Offene Nachweise

- Selkies: echter Mobile-Safari-/WebKit-Lauf auf einem physischen iPhone, Authentifizierung und Tailscale-HTTPS sowie ein bewusstes mobiles Client-Layout statt des nachgewiesenen Letterbox-POCs.
- Selkies: WebRTC-Modus unter derselben echten Browser- und Eingabeprobe; der hier dokumentierte Browser-E2E nutzt ausschließlich den WebSocket-Modus.
- Physisches iPhone: Safari über freigegebenes Tailscale Serve/HTTPS. Der konkrete Aktivierungsversuch wurde vom Tailnet mit `Serve is not enabled on your tailnet` abgelehnt; ein Administrator muss Serve freigeben, bevor eine ehrliche iPhone-URL und der Safari-E2E-Test möglich sind.
- Mehrfachmessung der kompletten Interaktionskette: fünf Start-zu-erster-nichtleerer-Frame- und Eingabeantwort-Läufe je Version, nicht nur API-Launches.

Bis diese Nachweise vorliegen, bleibt KasmVNC 1.3.3 mit noVNC 1.4.x und dem 1024-×-576-Profil die kleinste risikoarme und tatsächlich geprüfte Lösung.
