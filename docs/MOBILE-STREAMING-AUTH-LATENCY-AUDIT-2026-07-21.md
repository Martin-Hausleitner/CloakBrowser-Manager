# Kritischer Mobile-, Login- und Streaming-Audit

Stand: 21. Juli 2026

Dieser Audit bewertet den aktuellen r49-Stand des CloakBrowser Managers gegen die tatsächlich ausgeführten Browser-, Rollen- und Transportprüfungen. Er trennt nachgewiesene Funktion, lokale Messung und noch offene Produktbehauptung. Browser-Use diente nur als Interaktionsreferenz; fremde Marken-, Cloud- oder UI-Assets wurden nicht übernommen.

## Ergebnis in einem Satz

Für das mobile Web-MVP bleibt **KasmVNC 1.3.3 + noVNC 1.4.x bei 1024 × 576** die am besten nachgewiesene Basis: fünf mobile Viewpoints bestanden zusammen **249/249 Checks**, vier Authentifizierungswege erreichten einen echten verbundenen Canvas, und der warme lokale WebSocket-Upgrade lag in 20/20 Läufen bei **3,457 ms Median / 8,931 ms p95**. Das beweist noch keine physische iPhone-, WAN- oder Touch-to-Pixel-Latenz.

## Was jetzt nachgewiesen ist

| Bereich | Nachweis | Ergebnis |
|---|---|---|
| iPhone 14 Portrait | 390 × 844, echter VNC-Canvas, Split, Grid, Fullscreen, Viewport-Editor und CDP-beobachtete Pointer-/Touch-Probe | 51/51 |
| iPhone SE Portrait | 375 × 667, kurzer Viewport mit kompaktem Live-Anteil und vollständig erreichbarem Composer | 49/49 |
| iPhone Pro Max Portrait | 430 × 932, inklusive sichtbarem Inline-Viewport-Editor | 52/52 |
| iPhone 14 Landscape | 844 × 390, horizontaler Split und Fullscreen | 48/48 |
| Touch-Tablet | 768 × 1024, coarse pointer, Grid und Fullscreen | 49/49 |
| Vision-Artefakte | Empty, Workspace, Grid, Fullscreen und Fullscreen-Viewport; Pro Max zusätzlich Inline-Editor | 22 Screenshots |
| Legacy-Token | Login, Profilwahl und verbundener Canvas bei 390 × 844 | bestanden |
| Bootstrap-Token | Admin-Login, Profilwahl, verbundener Canvas und Viewport-Administration | bestanden |
| Operator-Konto | Nur zugewiesene Sandbox; Stream und sichtbare Remote-Werkzeuge; keine Profil-Viewport-Administration | bestanden |
| Viewer-Konto | Nur zugewiesene Sandbox; Stream und Fullscreen; Paste/Remote-Werkzeuge ausgeblendet, Start und Clipboard per API verweigert | bestanden |

Der Gate prüfte unter anderem genau einen Canvas, `Connected`, keinen horizontalen Overflow, mindestens 44 × 44 CSS-Pixel große sichtbare Controls, Live-Pane und visuellen noVNC-Zoom, Pointer-/Touch-Zielkoordinaten über eine CDP-beobachtete Probe, manuellen iOS-Paste-Fallback, Fullscreen-Fokus, `inert`-Hintergrund, Escape-Rückkehr, Grid, Chat-Verlauf und Composer. Eine Keyboard-/RFB-bis-CDP-Messung wurde in diesem r49-Artefakt nicht aufgezeichnet.

Lokale Belege:

- `artifacts/mobile-ui-gate-r49-final-venv/report.json`
- `artifacts/streaming-login-audit-r49/auth-api-summary.json`
- `artifacts/streaming-login-audit-r49/auth-ui-summary.json`
- `artifacts/streaming-benchmark-r49/streaming-benchmark-report.json`

## Kritische Fehler, die der Audit gefunden und behoben hat

### 1. Sandbox-Zuweisungen ließen sich im Dashboard nicht zuverlässig ändern

`PUT /api/access/users/{id}` erhielt verschachtelte Grants nach der Pydantic-Serialisierung als Dictionaries, behandelte sie aber erneut wie Modelle. Das führte beim realen Dashboard-Payload zu `500 AttributeError`. Der Endpoint akzeptiert jetzt beide gültigen Formen; ein Regressionstest deckt den Browser-Payload ab. Der frische API-Lauf aktualisierte Operator und Viewer jeweils mit HTTP 200.

### 2. Login-Controls waren auf Mobilgeräten zu klein

Account-Eingaben, Token-Eingabe, Hauptaktion und Moduswechsel lagen teilweise unter dem 44-Pixel-Touchziel. Alle vier Control-Typen sind jetzt mindestens 44 Pixel hoch. Der reale 390-×-844-Lauf maß für jedes sichtbare Login-Control exakt 44 Pixel ohne horizontalen Overflow.

### 3. Operator und Viewer sahen eine scheinbar editierbare Remote-Viewport-Aktion

Pane-Ratio und visueller Zoom sind lokale Live-Funktionen und bleiben für alle berechtigten Stream-Viewer verfügbar. Die Änderung der echten Profilauflösung ist dagegen Profiladministration. Inline- und Fullscreen-Viewport-Editor werden deshalb nur Administratoren gezeigt; Operator und Viewer sehen weiterhin Live-View und Fullscreen, aber keine irreführende Admin-Aktion.

## Live-Anpassung: was sofort wirkt und was einen Neustart braucht

| Einstellung | Wirkung | Aktueller Zustand |
|---|---|---|
| Browser-Pane-Ratio | verändert den Split sofort | live, 42–82 % |
| Visueller noVNC-Zoom | verändert sichtbare Canvas-Geometrie sofort, ohne CSS-Transform | live, 75–150 % |
| View-Reset | setzt Pane und Zoom geräteabhängig zurück | live |
| Fullscreen View/Zoom | bleibt im Fullscreen erreichbar | live |
| Profil-Viewport Breite/Höhe | speichert die Xvnc-/Browserauflösung | wirkt beim nächsten Profilstart |
| Phone-fit-Preset | übernimmt den aktuellen Visual Viewport als Profilwert | speichert für den nächsten Start |

Die Oberfläche sagt bei einem laufenden Profil ausdrücklich: **„Saves for the next launch; visual zoom changes now“**. Ein echter Live-Resize des laufenden Xvnc-Framebuffers ist nicht implementiert und wird nicht als erledigt behauptet.

## Frische Latenzmessung

Der r49-Runner verwendete eine bereits laufende, loopback-gebundene 1024-×-576-Session und 20 warme Wiederholungen.

| Messpunkt | Erfolg | Median | p95 | Maximum |
|---|---:|---:|---:|---:|
| Manager HTTP First Byte | 20/20 | 1,438 ms | 4,005 ms | 14,794 ms |
| VNC-WebSocket-Upgrade | 20/20 | 3,457 ms | 8,931 ms | 49,980 ms |

Diese Zahlen messen TCP/HTTP beziehungsweise den WebSocket-Upgrade. Sie messen **nicht** Browserstart, ersten Bildinhalt, Frame-Rate, Berührung bis Pixeländerung, Tailnet/WAN oder Mobilfunk.

### Einordnung der im r49-Lauf gelisteten Stacks

| Stack | r49-Status | Entscheidung |
|---|---|---|
| KasmVNC 1.3.3 + noVNC 1.4.x | vollständiger aktueller Produktpfad, 249 Mobile-Checks, vier Loginpfade | beibehalten |
| Selkies | `not_installed`, keine aktuelle Messung | erst nach gleichwertiger Provisionierung vergleichen |
| Sunshine/Moonlight | `architecture_only`, keine aktuelle Messung | nicht als Latenzvergleich werten |
| Apache Guacamole | `architecture_only`, keine aktuelle Messung | nicht als Latenzvergleich werten |

Frühere isolierte KasmVNC-1.4- und Selkies-POCs sind in `docs/REMOTE-STREAMING-BENCHMARK.md` als historische, methodisch nicht gleichwertige Experimente dokumentiert; sie sind kein Bestandteil des r49-Vergleichs. Einen technologieübergreifenden „Latenz-Sieger“ zu behaupten wäre deshalb falsch. KasmVNC ist die aktuelle Produktempfehlung, weil nur dieser Pfad vollständig integriert und aktuell end-to-end geprüft ist.

## Empfohlene Chrome-/Profilbasis für Mobile

1. **1024 × 576 als verifizierten Standard behalten.** Genau diese laufende Profilauflösung bestand die frischen Mobile-, Auth- und warmen Loopback-Gates; der r49-Lauf enthält keinen neuen 1440-×-900-Vergleich.
2. **Pane und noVNC-Zoom für spontane Anpassungen nutzen.** Sie reagieren live und halten den Remote-Browserprozess stabil.
3. **390 × 844 oder Phone-fit bewusst pro Profil speichern**, wenn die Zielseite ihre echte Mobile-Responsive-Variante liefern soll; anschließend Profil neu starten.
4. **1440 × 900 nicht aus dem r49-Lauf bewerten.** Bei einer konkreten Desktop-Layout-Anforderung separat auf demselben Gerät und Transport messen.
5. **Browserfenster an den VNC-Framebuffer anpassen.** Der integrierte PR-47-Pfad hält Chrome innerhalb des sichtbaren Desktops.
6. **Mobile User-Agent nicht global erzwingen.** Viewport, Plattform/Fingerprint und gewünschte Website-Variante müssen pro Profil zusammenpassen.
7. **Keine unsicheren Chrome-Flags zur vermeintlichen Beschleunigung hinzufügen.** Sandbox, Origin- und Auth-Grenzen bleiben Teil des Produktpfads.
8. **Adaptive Qualität erst mit echter Frame-/Input-Telemetrie automatisieren.** Ein schneller Handshake allein ist kein Signal für gute Bild- oder Eingabelatenz.

## Die zehn besten nächsten Eingabe-Implementierungen

| Rang | Implementierung | Warum sie den größten Nutzen bringt | Abnahmekriterium |
|---:|---|---|---|
| 1 | **iOS-IME-Bridge über fokussierbares Hidden Textarea** | öffnet die native Tastatur zuverlässig und unterstützt Komposition, Autokorrektur und Sonderzeichen statt nur einzelner Key-Events | `beforeinput`/composition bis RFB oder CDP; Umlaute, Emoji und CJK auf iPhone geprüft |
| 2 | **Kompakte Keyboard-Zubehörleiste** | Esc, Tab, Enter, Pfeile, Cmd/Ctrl, Alt und Backspace fehlen auf der iPhone-Tastatur oder sind umständlich | einhändig erreichbar, 44-Pixel-Ziele, Sticky nur bei aktivem Input |
| 3 | **Trackpad-/Direkt-Touch-Umschalter** | Desktop-Seiten brauchen präzisen Cursor, mobile Seiten direkte Taps | sichtbarer Modus, Cursor-Speed, Tap/Drag/Right-click und Haptik geprüft |
| 4 | **Explizite Gesten-Zustandsmaschine** | verhindert Konflikte zwischen Remote-Scroll, Pinch-Zoom, Pane-Resize und Browsernavigation | ein Finger = Pointer, zwei Finger = Scroll, Pinch = lokaler Zoom; keine Ghost-Clicks |
| 5 | **Paste Sheet 2.0 mit Queue und Zielanzeige** | baut auf dem vorhandenen manuellen iOS-Paste-Fallback auf und macht Mehrzeiler/Passwörter kontrollierbar | Preview, Maskierung, Länge, Ziel-Sandbox, Erfolg/Fehler und kein stilles Doppel-Senden |
| 6 | **Interaction Lock / „Tap to control“** | verhindert versehentliche Remote-Klicks beim Scrollen im Chat oder beim Gerätewechsel | Viewer immer gesperrt; Operator entsperrt bewusst; Zustand prominent und pro Reconnect sicher |
| 7 | **Touch-to-Ack-Latenztelemetrie** | liefert erstmals echte Eingabelatenz statt nur Transport-Handshake | p50/p95 von Touch über RFB/CDP bis DOM-/Pixel-Ack, getrennt nach Tailnet/WLAN/Mobilfunk |
| 8 | **Reconnect- und Input-Replay-Schutz** | alte Taps oder Tastendrücke dürfen nach einer Unterbrechung nicht verspätet im Browser landen | Queue wird bei Disconnect sichtbar verworfen; neue Eingabe erst nach Session-ID-Wechsel |
| 9 | **Voice-Zielschalter: Task oder Remote-Feld** | Sprache ist mobil schnell, darf aber nicht versehentlich in die falsche Oberfläche schreiben | klarer Zielmodus, Vorschau vor Senden, keine Aufnahme ohne sichtbaren Zustand |
| 10 | **Policy-sichere Shortcuts und Makros** | wiederkehrende Navigation wird schneller, muss aber Sandbox- und Rollenrechte respektieren | allow-listed Aktionen, Parameter-Vorschau, Audit-Event und keine Ausführung für Viewer |

## Noch nicht erfüllt oder nicht ehrlich beweisbar

- Kein physischer iPhone-/Mobile-Safari-E2E. Chromium-Device-Emulation und macOS-WebKit ersetzen ihn nicht.
- Safari Remote Automation war lokal nicht freigegeben; das vorbereitete WebKit-Gate meldete diesen Zustand korrekt als blockiert.
- Tailscale Serve ist im Tailnet nicht aktiviert. Es existiert daher noch keine geprüfte private HTTPS-iPhone-URL.
- Kein echter WAN-/Tailnet-/Mobilfunk- und kein Touch-to-Pixel-p50/p95-Bericht.
- Der Grid-View ist ein schneller Session-/Profilumschalter, kein gleichzeitiges Multi-Canvas-Monitoring.
- Der Chat-Composer ist eine lokale Produkt-Demo und noch nicht an eine echte Browser-Agent-Task-API angeschlossen.
- Selkies, Sunshine/Moonlight und Guacamole sind im aktuellen r49-Lauf nicht als gleichwertige Produktdeployments provisioniert; fehlende Zeiten bleiben bewusst leer.

## Freigabeempfehlung

Der r49-Stand ist als **lokales, rollenbasiertes Mobile-Web-MVP** freigabefähig. Für eine externe oder iPhone-spezifische Freigabe fehlen noch Tailscale Serve/HTTPS, ein physisches Safari-Gerät sowie echte Touch-to-Pixel- und Reconnect-Messungen. Die höchste nächste Produktpriorität ist die iOS-IME-Bridge, gefolgt von Keyboard-Zubehörleiste und echter Eingabelatenztelemetrie.
