# Mobile E2E-Validierung

Stand: 20. Juli 2026

## Ergebnis

Der Produktionscontainer und der echte KasmVNC/noVNC-Stream wurden mit dem reproduzierbaren Runner `scripts/mobile_ui_gate.py` geprüft. Alle vier Viewports und insgesamt 87 automatisierte Assertions waren grün:

| Viewport | Layout | Workspace bereit | Live-Verbindung | Ergebnis |
|---|---|---:|---:|---|
| iPhone 14, 390 × 844 | vertikaler Split | 147,5 ms | 37,9 ms | bestanden |
| iPhone Pro Max, 430 × 932 | vertikaler Split | 118,0 ms | 35,4 ms | bestanden |
| iPhone 14 Landscape, 844 × 390 | 58/42 Side-by-Side | 132,2 ms | 38,3 ms | bestanden |
| Touch-Tablet, 768 × 1024 | vertikaler Split | 133,6 ms | 37,8 ms | bestanden |

Die Tabelle stammt aus dem finalen Wiederholungslauf gegen einen bereits laufenden Browser. In einem unmittelbar vorangehenden Lauf startete derselbe Build das gestoppte Testprofil und erreichte die erste Live-Verbindung nach 4.904,8 ms. Dieser Einzelwert ist ein E2E-Smoke-Nachweis, kein belastbarer Median.

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
- Der noVNC-Canvas erhielt Fokus; `Ctrl+L`, URL-Zeichen und Enter wurden als deterministische Keyboard-Events durch RFB gesendet. Der CDP-Proxy meldete anschließend exakt `http://127.0.0.1:8080/api/status`, und der VNC-Screenshot zeigte die JSON-Antwort.
- Vollbild deckt den Viewport ab, setzt den Hintergrund `inert` und fokussiert die Schließen-Aktion.
- Escape schließt Vollbild, gibt den Fokus zurück und erzeugt keinen Overflow.
- Dreizehn PNG-Artefakte besitzen die erwartete Viewportgröße und eine nichttriviale Dateigröße.

## Frische Code-Gates

| Gate | Ergebnis |
|---|---|
| Frontend | 4 Dateien, 31 Tests bestanden |
| Backend | 187 Tests bestanden; eine Starlette-Deprecation-Warnung |
| Produktionsbuild | bestanden |
| App-JS | 75,92 kB gzip bei Budget 85 kB |
| lazy noVNC | 50,50 kB gzip bei Budget 60 kB |
| CSS | 5,20 kB gzip bei Budget 6 kB |
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
