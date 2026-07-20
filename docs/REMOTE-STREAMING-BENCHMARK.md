# Remote-Streaming-Benchmark und Stack-Empfehlung

Stand: 20. Juli 2026

Dieser Bericht trennt Messwerte, Funktionsnachweise und Architekturentscheidungen bewusst voneinander. Die Zahlen stammen aus lokalen Läufen auf derselben Entwicklungsmaschine, aber nicht aus einem vollständig kontrollierten Laboraufbau. Sie sind eine belastbare Richtungsentscheidung für das Mobile-MVP, kein allgemeines Produktversprechen.

## Kurzentscheidung

Für den aktuellen CloakBrowser Manager bleibt **KasmVNC 1.3.3 + noVNC 1.4.x bei 1024 × 576** die empfohlene Produktionsbasis.

- Der vorhandene Stack ist der einzige Kandidat, dessen vollständige Kette `Browser → X11 → KasmVNC → FastAPI-WebSocket-Proxy → noVNC → echter Canvas` lokal end-to-end nachgewiesen wurde.
- 1024 × 576 benötigt im warmen Vergleich deutlich weniger Startzeit und RAM als 1440 × 900.
- KasmVNC 1.4 bleibt der sinnvollste nächste isolierte Vergleich, benötigt nach dem behobenen `search_engine`-Drift aber noch einen vollständigen Wiederholungslauf.
- Selkies ist ein interessanter WebRTC/WebSocket-Video-POC. Der bisherige Lauf beweist Serverbereitschaft, aber noch keinen vollständigen Browserstream.
- Sunshine/Moonlight passt eher zu einem nativen Companion-Client als zu einem eingebetteten iPhone-Web-Viewer.
- noVNC 1.7 ist kein Drop-in-Update für den aktuellen Viewer.

## Vergleichsmatrix

| Kandidat | Gemessener/ermittelter Zustand | Evidenzgrad | Entscheidung |
|---|---|---|---|
| KasmVNC 1.3.3 + noVNC 1.4.x | Vollständiger echter Browserstream und Mobile-Vollbild lokal geprüft | E2E-verifiziert | Beibehalten |
| KasmVNC 1.4 | Image gebaut; isolierter vollständiger Launch nach Schema-Fix noch offen | Teilverifiziert | Nächster kontrollierter A/B-Test |
| noVNC 1.7 | Build scheitert ohne Migration an ESM-/Export-Änderungen und entfernter `showDotCursor`-API | Kompatibilitätsprüfung | Nicht direkt aktualisieren |
| Selkies | Serverstart für H.264/JPEG/WebRTC gemessen; vollständiges Browserbild nicht bewiesen | Server-Smoke-Test | Nur isolierter POC |
| Sunshine + Moonlight | Architektur und Clientmodell geprüft; kein passender allgemeiner Web-Embed-Pfad | Architekturprüfung | Kein Web-MVP-Core |
| Browser-Use Chat UI | Interaktions- und Informationsarchitektur geprüft | UI-Referenz | Als UX-Referenz, nicht als Streamingstack |

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

## Selkies: bisheriger POC

Das arm64-Image war ungefähr 1,245 GB groß. Der erste korrigierte Build benötigte ungefähr 85 Sekunden; danach waren die relevanten Layer gecacht.

| Modus | Zeit bis Server bereit | Idle-CPU | RAM |
|---|---:|---:|---:|
| H.264 | 7.278 ms | ca. 1–2,3 % | ca. 73–75 MiB |
| JPEG | 2.141 ms | ca. 1–2,3 % | ca. 73–75 MiB |
| WebRTC | 1.569 ms | ca. 1–2,3 % | ca. 73–75 MiB |

Wichtige Grenze: Der Test erreichte den Streamingserver, lieferte aber nur ein leeres Testbild. GPU- und Audio-Unterstützung waren in dieser Umgebung nicht vollständig verfügbar. Diese Zahlen dürfen daher nicht als Browser-E2E- oder First-Interactive-Werte mit KasmVNC verglichen werden.

## Sunshine/Moonlight

Sunshine ist auf macOS weiterhin ein experimenteller Hostpfad; Moonlight ist primär ein nativer Client. Das Modell eignet sich für sehr flüssige Fernsteuerung mit einem installierten Companion-Client, nicht für den geforderten eingebetteten VNC-/Web-Workspace in Mobile Safari. Ein allgemeiner offizieller Webclient, der den aktuellen noVNC-Embed ohne Produktumbau ersetzt, wurde nicht nachgewiesen.

Deshalb ist der Stack für dieses Produkt derzeit ein **Conditional No-Go**: nur erneut prüfen, wenn ein nativer iOS-Companion ausdrücklich Teil des Produkts wird.

## noVNC 1.7 und KasmVNC 1.4

Der direkte noVNC-1.7-Versuch traf auf geänderte ESM-Exports und die entfernte `showDotCursor`-Oberfläche. Eine Aktualisierung benötigt daher eine kleine Viewer-Migration und einen eigenen Reconnect-, Touch-, Fullscreen- und Safari-Test. Sie darf nicht gemeinsam mit einem KasmVNC-Upgrade als untrennbares Paket bewertet werden.

Das KasmVNC-1.4-Image ließ sich bauen. Ein damaliger App-Launch wurde von einem inzwischen behobenen Profilschema-/`search_engine`-Drift blockiert. Der nächste Test muss KasmVNC 1.3.3 und 1.4 mit demselben Profil, demselben Browsercache und denselben Messpunkten vergleichen.

## Reproduzierbares Messprotokoll für den nächsten A/B-Lauf

1. Identisches persistentes Testprofil mit 1024 × 576 und deaktiviertem Clipboard-Sync verwenden.
2. Browser- und Imagecache vor einem kalten Lauf dokumentieren; warme Läufe getrennt ausweisen.
3. Pro Kandidat mindestens fünf warme Wiederholungen durchführen.
4. Messen: API-Launch bis `running`, WebSocket-Upgrade, erster nichtleerer Frame, Zeit bis Eingabeantwort, CPU, RSS/RAM und übertragene Bytes.
5. Danach dieselbe Sequenz bei 1440 × 900 wiederholen.
6. Auf iPhone Safari zusätzlich Reconnect nach App-Hintergrund, Touch-Drag, Keyboard, Vollbild-Fallback und Tailscale-HTTPS prüfen.
7. Median, p95 und Ausreißer zusammen berichten; fehlgeschlagene Läufe nicht aus der Stichprobe entfernen.

Der Browser-/UI-Gate-Runner liegt unter `scripts/mobile_ui_gate.py`. Er prüft vier Viewports, Touch-Ziele, Overflow, Split-Geometrie, Demo-Composer, Grid, Fullscreen-Fokus und – mit einer Profil-ID – genau einen echten VNC-Canvas. Optional tippt `--remote-probe-url` eine harmlose, eindeutige URL per Keyboard-Events durch noVNC/RFB und verifiziert die Zielseite danach über den CDP-Proxy. Seine Screenshotprüfung validiert Abmessungen und Mindestdateigröße; die abschließende semantische Sichtprüfung bleibt bewusst ein separater menschlicher oder Vision-Agent-Gate.

## Gepinnte Referenzstände

Die folgenden `HEAD`-Stände wurden am 20. Juli 2026 direkt aus den öffentlichen Repositories gelesen:

- [KasmVNC @ `f77a5ca`](https://github.com/kasmtech/KasmVNC/tree/f77a5ca3e82f16709d93ae6b016d5baa0168208e)
- [noVNC @ `7c36fab`](https://github.com/novnc/noVNC/tree/7c36fabe599e053c5a81e98e091ac636f6c1e174)
- [Selkies GStreamer @ `44fb739`](https://github.com/selkies-project/selkies-gstreamer/tree/44fb7391901757605e0e617875fdfd4c9dda5906)
- [Sunshine @ `9d2409f`](https://github.com/LizardByte/Sunshine/tree/9d2409f71b60f1812f482e6dd807dc52e2f72fe7)
- [Moonlight Qt @ `2328713`](https://github.com/moonlight-stream/moonlight-qt/tree/2328713f4e7b8442e6bd49238b4eba27031a4d9f)
- [Browser-Use Chat UI @ `0a0e855`](https://github.com/browser-use/chat-ui-example/tree/0a0e855205bd90fb782c60e7dabe8149b8476acf)

## Offene Nachweise

- KasmVNC 1.4: kompletter identischer Browser-E2E-Wiederholungslauf.
- Selkies: echter CloakBrowser-Desktop statt leerem Testframe, inklusive iPhone-Webclient.
- Physisches iPhone: Safari über freigegebenes Tailscale Serve/HTTPS. Der konkrete Aktivierungsversuch wurde vom Tailnet mit `Serve is not enabled on your tailnet` abgelehnt; ein Administrator muss Serve freigeben, bevor eine ehrliche iPhone-URL und der Safari-E2E-Test möglich sind.
- Mehrfachmessung: Median/p95 statt einzelner lokaler Läufe.

Bis diese Nachweise vorliegen, bleibt der vorhandene KasmVNC/noVNC-Stack mit dem 1024-×-576-Profil die kleinste risikoarme und tatsächlich geprüfte Lösung.
