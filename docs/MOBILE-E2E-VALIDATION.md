# Mobile E2E-Validierung

Stand: 21. Juli 2026

## Ergebnis

Der Produktionscontainer und der echte KasmVNC/noVNC-Stream wurden mit dem reproduzierbaren Runner `scripts/mobile_ui_gate.py` geprüft. Alle vier Viewports und insgesamt 87 automatisierte Assertions waren grün:

| Viewport | Layout | Workspace bereit | Live-Verbindung | Ergebnis |
|---|---|---:|---:|---|
| iPhone 14, 390 × 844 | vertikaler Split | 147,5 ms | 37,9 ms | bestanden |
| iPhone Pro Max, 430 × 932 | vertikaler Split | 118,0 ms | 35,4 ms | bestanden |
| iPhone 14 Landscape, 844 × 390 | 58/42 Side-by-Side | 132,2 ms | 38,3 ms | bestanden |
| Touch-Tablet, 768 × 1024 | vertikaler Split | 133,6 ms | 37,8 ms | bestanden |

Die Tabelle stammt aus dem finalen Wiederholungslauf gegen einen bereits laufenden Browser. In einem unmittelbar vorangehenden Lauf startete derselbe Build das gestoppte Testprofil und erreichte die erste Live-Verbindung nach 4.904,8 ms. Dieser Einzelwert ist ein E2E-Smoke-Nachweis, kein belastbarer Median.

## Aktueller Kompatibilitäts-Wiederholungslauf

Nach dem Frontend-Kompatibilitätsfix für einen offenen Legacy-Backend-Status wurde der aktuelle Vite-Build erneut gegen denselben bereits laufenden KasmVNC/noVNC-Browser ausgeführt. **95/95 Prüfungen** bestanden: 26 im iPhone-14-Portrait-Lauf sowie je 23 auf iPhone Pro Max, iPhone Landscape und Touch-Tablet. Der Lauf prüfte den echten 1024-×-576-Canvas, Verbindungsstatus, CSS-Vollbild, Grid, die Browser-Use-inspirierte Demo-Composer-Interaktion, 44-px-Touch-Ziele und den kontrollierten Remote-Clipboard-/CDP-Pfad.

Zusätzlich bestanden im identischen Arbeitsstand der Produktionsbuild, **40 Frontend-Tests** und **194 Backend-Tests**. Der Lauf verwendete ausschließlich einen bereits vorhandenen Testbrowser und eine harmlose lokale Status-URL als Remote-Probe; Screenshot-Artefakte sind bewusst ignorierte lokale Testausgaben. Er erhöht die Wiederholbarkeit des Browser-E2E-Nachweises, ersetzt aber die unten genannte Abnahme auf physischem Mobile Safari über Tailscale-HTTPS nicht.

## Abgesicherter Policy- und Dashboard-Nachtest

Der aktuelle Source-Build wurde in einem frischen, isolierten Container mit ausschließlich `127.0.0.1:18081`-Bindung, aktivem `AUTH_TOKEN` und `ACCESS_CONTROL_ENABLED=1` ausgeführt. Ein erster vollständig frischer Lauf deckte auf dem iPhone-14-Viewport einen echten First-Connect-Fokusfehler auf: Nach einem Touch erhielt der noVNC-Canvas nicht zuverlässig sofort den Tastaturfokus. Der Viewer fokussiert deshalb jetzt bei Pointer- und Touch-Interaktion den dynamisch erzeugten Canvas; View-only bleibt davon ausgenommen.

Der daraufhin neu gebaute und erneut isoliert gestartete Nachtest bestand **100/100 Assertions**: **96** für die vier Workspace-/VNC-Viewports sowie **4** für den iPhone-14-großen Access-Dashboard-Gate. Die eine zusätzliche Workspace-Assertion ist der explizite Fokusnachweis im ersten iPhone-14-Lauf.

| Bereich | Nachweis | Ergebnis |
|---|---|---|
| iPhone 14, Pro Max, Landscape und Touch-Tablet | echter Canvas, VNC-Verbindung, Touch-/Pointer-Fokus, Paste, kontrollierter CDP-Keyboard-Pfad, Grid, Vollbild, Composer und 44-px-Touch-Ziele | 96/96 bestanden |
| Access Dashboard auf 390 × 844 | Dashboard-Aktion, Rendering, kein horizontaler Overflow und alle sichtbaren Buttons, Selects sowie Textinputs mindestens 44 × 44 px | 4/4 bestanden |

Der Dashboard-Gate ist Teil von `scripts/mobile_ui_gate.py` und wird mit `--access-dashboard` nur zusammen mit einem lokal referenzierten Token-Environment aktiviert. Tokenwerte erscheinen weder im Report noch in Screenshots. Ein repräsentativer iPhone-Workspace-Screenshot mit verbundenem Live-Canvas und ein iPhone-14-Screenshot des Access Dashboards wurden visuell kontrolliert. Der isolierte Testcontainer und seine Testdaten sind nicht die laufende Alltagsinstanz.

Diese Prüfung beweist die geschützte Browser-/VNC- und Dashboard-Oberfläche im Browser. Sie ersetzt nicht den noch offenen physischen iPhone-Safari-Test über private Tailscale-HTTPS, weil Tailscale Serve in diesem Tailnet derzeit administrativ deaktiviert ist.

## Finaler Mobile-Overflow-Nachtest für das Access Dashboard

Die Mobile-UX wurde danach noch einmal gegen eine lange Sandbox-Kennung nachgetestet. Auslöser war ein echter iPhone-14-Befund: Das zweispaltige Dashboard-Grid erzeugte auf 390 px eine implizite zu breite Spalte. Die Oberfläche verwendet dort nun explizit einen einspaltigen Grid-Pfad; Eingabefelder und Schlüsselbereich dürfen schrumpfen beziehungsweise umbrechen.

Der frische authentifizierte Nachtest lief in einem isolierten aktuellen Container ohne ausgewähltes Live-Profil. Deshalb ist er ein Layout-/Dashboard-Gate und ersetzt **nicht** die oben dokumentierte Live-VNC-Abnahme. Alle **73/73** Prüfungen bestanden:

| Bereich | Nachweis | Ergebnis |
|---|---|---|
| iPhone 14, Pro Max, Landscape und Touch-Tablet | Workspace-Struktur, Split-Geometrie, Chat/Composer, Grid, Vollbild, Fokus-Rückgabe, kein Overflow und 44-px-Touch-Ziele | 68/68 bestanden (17 je Viewport) |
| Access Dashboard auf 390 × 844 | Aktion, Rendering, langer Sandbox-Name ohne horizontalen Overflow, alle sichtbaren Buttons, Selects und Textinputs mindestens 44 × 44 px sowie Screenshot-Artefakt | 5/5 bestanden |

Für das Dashboard betrugen `scrollWidth` und `clientWidth` jeweils exakt 390 px. Der iPhone-14-Screenshot wurde nach dem automatisierten Lauf visuell kontrolliert: Die Kennung ist sinnvoll gekürzt, die Bedienelemente bleiben erreichbar und es gibt keinen seitlichen Scrollbereich. Der vollständig getrennte Live-VNC-Nachweis bleibt der r20-Lauf mit verbundenem Canvas und RFB-/CDP-Eingabeprobe.

Zum dokumentierten Endstand bestanden außerdem der Produktionsbuild, **63 Frontend-Tests** und **220 Backend-Tests** (eine bekannte Starlette-Deprecation-Warnung). Der spezifische Streaming-Runner-Test und die Python-Kompilationsprüfung des Mobile-Gate-Runners waren ebenfalls grün.

## Safari/WebKit-Gate (vorbereitet, lokale Freigabe ausstehend)

Zusätzlich liegt nun `scripts/mobile_webkit_gate.py` vor. Es startet optional einen ausschließlich loopback-gebundenen SafariDriver, prüft auf 390 × 844 die Mobile-Shell, die Navigation zum Benchmark-Report, Kandidaten-/Statuskennzeichnungen, horizontalen Overflow und ein Screenshot-Artefakt. Das Gate ergänzt den Chromium-/Live-VNC-Lauf; Safari auf macOS ist WebKit, aber weiterhin kein Ersatz für Mobile Safari auf einem physischen iPhone.

Der erste reale Lauf am 21. Juli 2026 war bewusst **nicht grün**: SafariDriver war lokal bereit, lehnte die Session aber mit `You must enable 'Allow remote automation'` ab. Der Runner schrieb daher den Status `blocked` in sein lokales Report-Artefakt und änderte keine Safari-Einstellung. Insbesondere wurde `safaridriver --enable` nicht ausgeführt. Sobald Remote Automation bewusst in Safari Settings → Developer aktiviert wurde, lässt sich der Nachtest reproduzierbar ausführen:

```bash
python3 scripts/mobile_webkit_gate.py \
  --base-url http://127.0.0.1:8080/ \
  --output-dir artifacts/mobile-webkit-gate \
  --start-driver \
  --min-benchmark-cards 5
```

Der isolierte Vertrags-Test `scripts/test_mobile_webkit_gate.py` simuliert den WebDriver-Austausch und prüft den erfolgreichen Report-, Redaktions- und Screenshotpfad ohne eine Safari- oder Netzwerkfreigabe zu benötigen.

## Nachtest: iOS-Paste-Fallback

Der aktuelle Source-Build wurde anschließend über den lokalen Vite-Proxy gegen denselben laufenden Testbrowser geprüft. Alle vier Viewports bestanden erneut. Zusätzlich öffnete jeder Durchlauf den manuellen **Paste text**-Dialog, validierte alle sichtbaren Touch-Ziele mit mindestens 44 × 44 CSS-Pixeln und bestätigte Browser → API → Remote-Clipboard bei weiterhin verbundenem VNC-Canvas. Der zugehörige Frontend-Test deckt die vollständige RFB-`Ctrl+V`-Sequenz ab.

Der Nachtest prüft den manuellen Fallback unabhängig von `navigator.clipboard`, damit eine fehlende oder von Mobile Safari abgelehnte Clipboard-Berechtigung keinen Paste-Flow blockiert. Der Report speichert keine Clipboard-Inhalte, sondern nur Match-Status und Längen eines nicht sensitiven Einmal-Markers. Die Artefakte lagen ausschließlich temporär außerhalb des Git-Repositories; repräsentative Workspace- und Vollbild-Screenshots wurden visuell kontrolliert.

## Isolierter KasmVNC-A/B-Nachtest

Die komplette Suite wurde zusätzlich gegen je einen isolierten KasmVNC-1.3.3- und KasmVNC-1.4.0-Container mit gleichem 1024-×-576-Profil ausgeführt. Beide Varianten bestanden vier Viewports, exakt einen echten VNC-Canvas, Grid, Vollbild, den lokalen Browser-Use-inspirierten Composer sowie die RFB-Keyboard-zu-CDP-Probe; pro Variante waren **88/88 Checks** und **13 Screenshots** grün.

| Variante | erster Start-zu-`Connected` | weitere drei Viewports | Ergebnis |
|---|---:|---:|---|
| KasmVNC 1.3.3 | 1.381,3 ms | 35,7 / 34,7 / 39,4 ms | bestanden |
| KasmVNC 1.4.0 | 3.145,7 ms | 34,2 / 35,1 / 35,5 ms | bestanden |

Der erste Wert je Zeile startete aus einem gestoppten Profil und ist deshalb nur ein einzelner Interaktions-Smoke-Test. Er ersetzt keinen Median, liefert aber keinen Grund, die bewährte 1.3.3-Basis zu migrieren. Die zugehörigen lokalen Artefakte liegen außerhalb des Git-Repositories unter `outputs/kasm13-mobile-e2e-20260720` und `outputs/kasm14-mobile-e2e-20260720-r2`.

## Geprüfte Gates

- Mobile Workspace statt Desktop-UI, einschließlich Landscape und 768-px-Touch-Tablet.
- Root-Geometrie entspricht dem jeweiligen Visual Viewport.
- Kein horizontaler Body-Overflow.
- Vertikaler beziehungsweise horizontaler Split liegt ohne Lücke oder Überlappung aneinander.
- Browserfläche, Task-Verlauf und Composer sind vorhanden.
- Attach-, Run-Settings-, Demo-Modell- und Run-Task-Control sind programmatisch erreichbar.
- Alle sichtbaren Buttons, Selects, Textareas und normalen Inputs sind mindestens 44 × 44 px groß.
- Lokale Chat-Nachricht und ausdrücklich gekennzeichnete Demo-Antwort funktionieren.
- Run Settings öffnen; das Demo-Modell lässt sich ändern.
- Grid öffnet und rendert den aktuellen Profilzustand.
- Der echte VNC-Status erreicht `Connected`.
- Vor, während und nach CSS-Vollbild existiert exakt ein Canvas.
- Der noVNC-Canvas erhält bei Pointer-/Touch-Interaktion Fokus; `Ctrl+L`, URL-Zeichen und Enter werden als deterministische Keyboard-Events durch RFB gesendet. Der CDP-Proxy bestätigt anschließend die harmlose eindeutige HTTP(S)-Probe, und der VNC-Screenshot zeigt die geladene Zielseite.
- Vollbild deckt den Viewport ab, setzt den Hintergrund `inert` und fokussiert die Schließen-Aktion.
- Escape schließt Vollbild, gibt den Fokus zurück und erzeugt keinen Overflow.
- Dreizehn PNG-Artefakte besitzen die erwartete Viewportgröße und eine nichttriviale Dateigröße.

## Frische Code-Gates

| Gate | Ergebnis |
|---|---|
| Frontend | 5 Dateien, 43 Tests bestanden |
| Backend | 200 Tests bestanden; eine Starlette-Deprecation-Warnung |
| Produktionsbuild | bestanden |
| App-JS | 81,13 kB gzip bei Budget 85 kB |
| lazy noVNC | 50,50 kB gzip bei Budget 60 kB |
| CSS | 5,58 kB gzip bei Budget 6 kB |
| Container | gesund |
| Serverlog-Smoke | keine Treffer für Error, Traceback, Exception, Securityfailure oder Reconnect-Loop im geprüften Zeitfenster |

## Visuelle Sichtprüfung

Die repräsentativen Screenshots wurden nach dem automatisierten Lauf geöffnet und visuell kontrolliert:

- iPhone Portrait zeigt den echten Browser oberhalb von Task-Feed und Composer; der Composer bleibt vollständig sichtbar.
- Fullscreen behält denselben verbundenen Stream. Die vertikale Letterbox ist bei einem 16:9-Remote-Viewport in einem hohen iPhone-Viewport erwartbar und verzerrt das Bild nicht.
- iPhone Landscape verwendet den vorgesehenen Side-by-Side-Split; Browser und Task-Composer bleiben gleichzeitig bedienbar.
- Das Tablet-Grid bleibt innerhalb der Breite; Profilstatus und Composer überlappen nicht.
- Keine abgeschnittenen Hauptaktionen, kein doppelter Viewer und kein horizontaler Scrollbereich wurden beobachtet.

Lokale Artefakte des finalen Laufs liegen im Ordner `outputs/mobile-quality-gate-20260720-release` außerhalb des Git-Repositories. Der JSON-Bericht heißt `report.json`; zwölf Bilder folgen dem Muster `<viewport>-<workspace|grid|fullscreen>.png`, ergänzt um `iphone-14-portrait-remote-input.png`.

## Ehrliche Grenzen

- Der Runner nutzt echtes Chromium und eine deterministische Coarse-Pointer-Emulation. Er ersetzt noch keine Abnahme auf einem physischen iPhone mit Mobile Safari.
- Der Remote-Input-Nachweis nutzt deterministische Browser-Automation gegen den fokussierten Canvas. Er beweist den noVNC/RFB/CDP-Pfad, ersetzt aber noch keinen physischen Touch- und Software-Tastaturtest auf Mobile Safari.
- Der Chat, Attach und Run Settings bleiben absichtlich lokale Demo-Funktionen, bis eine echte Run-/Task-API angeschlossen wird.
- Der Versuch, den lokalen Port 8080 mit Tailscale Serve/HTTPS tailnet-intern freizugeben, wurde mit `Serve is not enabled on your tailnet` abgelehnt. Ein Tailnet-Administrator muss Serve aktivieren; erst danach können die HTTPS-URL und der physische iPhone-Safari-Lauf nachgewiesen werden.
