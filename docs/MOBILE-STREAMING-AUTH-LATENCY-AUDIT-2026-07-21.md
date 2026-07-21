# Kritischer Mobile-, Login- und Streaming-Audit

Stand: 21. Juli 2026

Dieser Audit bewertet den final geprüften r56-Stand des CloakBrowser Managers und ordnet die älteren r49-/r50-Basen ein. Er trennt nachgewiesene Funktion, lokale Messung, Tailnet-Transport und noch offene Produktbehauptung. Browser-Use diente nur als Interaktionsreferenz; fremde Marken-, Cloud- oder UI-Assets wurden nicht übernommen.

## Ergebnis in einem Satz

Für das mobile Web-MVP bleibt **KasmVNC 1.3.3 + noVNC 1.4.x** die am besten nachgewiesene Basis: Der authentifizierte r56-Gate bestand fünf mobile Viewpoints plus Access-Dashboard mit **291/291 Checks** und **23 Screenshots**, während die ältere r49-Auth-Suite vier Authentifizierungswege bis zu einem echten verbundenen Canvas belegte. Im frischen browserbeobachteten Fünferlauf erreichte der vollständige lokale KasmVNC/noVNC-Produktpfad den ersten nichtschwarzen Frame nach **180 ms Median**, Guacamole nach **351 ms** und Selkies nach **5.272 ms**; der Selkies-Erstlauf lag bei 296 ms, vier Reloads jedoch nahe 5,3 s. Diese Werte sind wegen der unterschiedlichen Integration nur richtungsweisend. Ein zusätzlicher Codex-Computer-Use-Lauf bediente die finale iPhone-Ansicht real und bestätigte Canvas, vier persistente Aktionen, Rechte-Dashboard und den strikt verifizierten Hostvertrag. Das beweist weiterhin keine physische iPhone-, WAN-, FPS- oder Touch-to-Pixel-Latenz.

## r56-Endstand: UI-Architektur und Codex Computer Use

Der belegte r56-Stand ist eine Umstrukturierung und vollständige lokale Abnahme der mobilen Oberfläche, nicht ein allgemeiner Performance-Sieg:

- **Browser-first:** Der Live-Browser ist die primäre Fläche. Chat startet bei laufendem Browser collapsed, damit VNC nicht von Steuerleisten verdrängt wird.
- **Zentrale Tools:** Browser-Werkzeuge, Viewport, Zoom, Fullscreen, Sessions und Kontoaktionen sind in einem einzigen Tool-Sheet zusammengeführt. Benchmarks werden nicht in der mobilen UI angezeigt.
- **Quick actions statt Bookmarks:** Capture, Copy und Paste sind typisierte Hostaktionen und keine gespeicherten URLs. Sie werden nur aktiviert, wenn der verifizierte Host die passende Capability meldet.
- **Shortcuts:** Chat-Collapse und Fullscreen-Preview sind als schnelle Bedienwege vorgesehen, ohne Touch-only Bedienung zu erzwingen.
- **Grid:** Grid bleibt ein kompakter Session-/Profilumschalter und soll nicht still mehrere Streams starten, weil das iPhone-FPS und Akku verfälschen würde.
- **Codex-only Hostvertrag:** Der Composer akzeptiert nur eine injizierte Bridge, deren Capabilities explizit `provider: codex-computer-use` melden. Fehlende, generische oder nur umbenannte Harnesses bleiben deaktiviert; auch `send()` kann die Prüfung nicht umgehen.
- **Chat-Anbindung:** Der freigegebene UI- und Testpfad verwendet Codex Computer Use, hält Browser-Credentials außerhalb des Chats und simuliert keinen lokalen Erfolg. Der r56-Stand behauptet noch keine produktive externe Agent-Task-API ohne einen realen Host-Bridge-Prozess.
- **Rechte-Dashboard:** Browsersteuerung (`view`/`interact`/`operate`) und CDP-Automation sind getrennte Controls. Dadurch ist `operate + automate` auf derselben Sandbox möglich; eine einklappbare Vorschau zeigt die tatsächlich erreichbaren Profile und effektiven Fähigkeiten.

Der finale r56-Mobile-Gate ist abgeschlossen: fünf Viewports plus Access-Dashboard, **291/291 Checks**, **23 Screenshots**, keine Fehler. Er prüft Touch-Ziele, Fullscreen, Grid, Viewport/Zoom, Chat-Collapse, den einzeiligen Composer, Kontoaktionen nur im Tool-Sheet, Clipboard/Paste, eine echte VNC-Verbindung und die authentifizierte Access-Oberfläche. Ein separater Codex-Computer-Use-Lauf bestätigte den Release-Container zusätzlich über echte UI-Interaktionen und vergab einem Paperclip-Agenten kombiniert `operate + automate`.

## Was jetzt nachgewiesen ist

| Bereich | Nachweis | Ergebnis |
|---|---|---|
| iPhone 14 Portrait | 390 × 844, echter VNC-Canvas, Split, Grid, Fullscreen, Viewport-Editor und Codex-Computer-Use-Composer | 58/58 |
| iPhone SE Portrait | 375 × 667, kurzer Viewport mit kompaktem Live-Anteil und vollständig erreichbarem Composer | 57/57 |
| iPhone Pro Max Portrait | 430 × 932, inklusive sichtbarem Inline-Viewport-Editor | 59/59 |
| iPhone 14 Landscape | 844 × 390, horizontaler Split und Fullscreen | 56/56 |
| Touch-Tablet | 768 × 1024, coarse pointer, Grid und Fullscreen | 56/56 |
| Access-Dashboard | authentifizierter Adminpfad, kompakte Grants und mobile Overflow-Prüfung | 5/5 |
| Vision-Artefakte | Empty, Workspace, Grid, Fullscreen, Fullscreen-Viewport und Access-Dashboard | 23 Screenshots |
| Legacy-Token | Login, Profilwahl und verbundener Canvas bei 390 × 844 | bestanden |
| Bootstrap-Token | Admin-Login, Profilwahl, verbundener Canvas und Viewport-Administration | bestanden |
| Operator-Konto | Nur zugewiesene Sandbox; Stream und sichtbare Remote-Werkzeuge; keine Profil-Viewport-Administration | bestanden |
| Viewer-Konto | Nur zugewiesene Sandbox; Stream und Fullscreen; Paste/Remote-Werkzeuge ausgeblendet, Start und Clipboard per API verweigert | bestanden |

Der Gate prüfte unter anderem einen echten Canvas, `Connected`, keinen horizontalen Overflow, mindestens 44 × 44 CSS-Pixel große sichtbare Controls, Live-Pane und visuellen noVNC-Zoom, Pointer-/Touch-Zielkoordinaten über eine CDP-beobachtete Probe, manuellen iOS-Paste-Fallback, Fullscreen-Fokus, `inert`-Hintergrund, Escape-Rückkehr, Grid, Chat-Verlauf und den Codex-Computer-Use-Composer. Eine echte WAN-Touch-to-Pixel-Messung wurde in diesem lokalen Artefakt nicht aufgezeichnet.

Lokale Belege:

- `artifacts/mobile-ui-gate-r56-auth-final-r2/report.json`
- `artifacts/selkies-benchmark/r55-local/streaming-benchmark-report.json`
- `artifacts/guacamole-benchmark/r55-local/streaming-benchmark-report.json`
- `artifacts/streaming-login-audit-r49/auth-api-summary.json`
- `artifacts/streaming-login-audit-r49/auth-ui-summary.json`
- `artifacts/streaming-benchmark-r49/streaming-benchmark-report.json`

## Kritische Fehler, die der Audit gefunden und behoben hat

### 1. Sandbox-Zuweisungen ließen sich im Dashboard nicht zuverlässig ändern

`PUT /api/access/users/{id}` erhielt verschachtelte Grants nach der Pydantic-Serialisierung als Dictionaries, behandelte sie aber erneut wie Modelle. Das führte beim realen Dashboard-Payload zu `500 AttributeError`. Der Endpoint akzeptiert jetzt beide gültigen Formen; ein Regressionstest deckt den Browser-Payload ab. Der frische API-Lauf aktualisierte Operator und Viewer jeweils mit HTTP 200.

Der r50-Nachtrag behebt denselben Payload-Fehler auch für `PUT /api/access/agents/{id}`. Paperclip-Agent-Grants können jetzt mit Dashboard-Payloads aktualisiert werden; ein Regressionstest deckt die Aktualisierung von `view` auf `automate` ab.

Der r51-Nachtrag behebt außerdem zwei Mehrfachgrant-Fehler: Das Dashboard löscht beim Setzen der Steuerstufe nicht mehr ein vorhandenes `automate`, und die App prüft nicht mehr nur den ersten Grant einer Sandbox. Die gemeinsame Frontend-Policy spiegelt jetzt die serverseitige Vererbung. Abgelehnte REST- und WebSocket-Policyentscheidungen werden als redigierte Audit-Metadaten protokolliert.

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
| Profil-Viewport Breite/Höhe | speichert die Xvnc-/Browserauflösung | laufendes Profil wird kontrolliert neu gestartet |
| Phone-fit-Preset | übernimmt den aktuellen Visual Viewport als Profilwert | laufendes Profil wird kontrolliert neu gestartet |

Pane und Zoom reagieren ohne Server-Neustart. Eine neue echte Xvnc-/Browserauflösung kann KasmVNC in diesem Produktpfad nicht sicher in place übernehmen; `Apply` speichert deshalb den Wert und startet nur das ausgewählte laufende Profil kontrolliert neu. Die Oberfläche zeigt währenddessen **„Restarting live browser…“** und meldet Stop-/Relaunch-Fehler, statt eine nur gespeicherte Größe als live angewendet darzustellen.

## Frische Latenzmessung

Der r49-Runner verwendete eine bereits laufende, loopback-gebundene 1024-×-576-Session und 20 warme Wiederholungen.

| Messpunkt | Erfolg | Median | p95 | Maximum |
|---|---:|---:|---:|---:|
| Manager HTTP First Byte | 20/20 | 1,438 ms | 4,005 ms | 14,794 ms |
| VNC-WebSocket-Upgrade | 20/20 | 3,457 ms | 8,931 ms | 49,980 ms |

Diese Zahlen messen TCP/HTTP beziehungsweise den WebSocket-Upgrade. Sie messen **nicht** Browserstart, ersten Bildinhalt, Frame-Rate, Berührung bis Pixeländerung, Tailnet/WAN oder Mobilfunk.

### Aktueller VCVM-only-Produktpfad

Der aktuelle Manager, das React-UI, SQLite, CloakBrowser, Xvnc/KasmVNC und das Profilvolume liefen gemeinsam auf der VCVM; der Mac war ausschließlich Client über einen authentifizierten SSH-/Tailscale-Tunnel. Die VCVM veröffentlichte weder den Manager noch rohe VNC-Ports außerhalb von Loopback.

| Messpunkt | Läufe | Median | p95 | Maximum |
|---|---:|---:|---:|---:|
| VCVM `/health` total | 30 | 0,514 ms | 1,179 ms | 13,268 ms |
| VCVM authentifiziertes Profil-API total | 30 | 2,148 ms | 4,147 ms | 10,878 ms |
| VCVM VNC-WebSocket open | 20 | 6,689 ms | 9,989 ms | 70,799 ms |
| VCVM erstes RFB-Frame | 20 | 19,558 ms | 26,837 ms | 91,409 ms |
| Mac-Tunnel `/health` total | 30 | 61,8 ms | 125,6 ms | 150,7 ms |
| Mac-Tunnel VNC-WebSocket open | 15 | 195,2 ms | 266,8 ms | 502,5 ms |
| Mac-Tunnel erstes RFB-Frame | 15 | 204,6 ms | 328,6 ms | 555,1 ms |

Tailscale nutzte `DERP(nue)` mit 49–64 ms beobachtetem Ping und stellte keine direkte Verbindung her. `netcheck` zeigte auf dem Mac öffentliches IPv4 ohne IPv6, auf der VCVM dagegen öffentliches IPv6 ohne gefundenes IPv4; damit fehlt aktuell ein gemeinsamer direkter Adresspfad. Eine fünfsekündige synthetische Bewegungsseite ergab auf dem verbundenen Canvas **6,96 sichtbare Änderungen/s** VCVM-lokal und **4,18/s** über den Mac-Relay-Pfad. Das ist ein reproduzierbarer visueller Update-Proxy, aber weder Codec-FPS noch Touch-to-Pixel-Latenz.

### r50-VCVM/Neko-Tailnet-Beleg

Zusätzlich wurde ein bereits laufender Neko/Chrome-Stack auf der VCVM über Tailscale geprüft. Diese Messung bewertet den Tailnet-Transport und den geschützten Login, nicht die CloakBrowser-noVNC-UI.

| Messpunkt | Ergebnis |
|---|---:|
| Tailscale-Pfad | DERP(nue), keine direkte Verbindung |
| Tailnet-Ping | p50 **59 ms**, beobachteter Bereich **45-153 ms** |
| VCVM-lokaler HTTP-Zugriff | p50 TTFB **1,318 ms**, p50 total **1,372 ms** |
| Mac zu VCVM per SSH-Tunnel | p50 TTFB **226,095 ms**, p50 total **230,826 ms** |
| Browser First Paint | **2.140 ms** |
| Browser FCP | **2.232 ms** |
| Browser Load | **2.953,6 ms** |

Codex Computer Use führte den geschützten Login erfolgreich aus und sah `/ws`. WebRTC ICE blieb jedoch `checking` und wechselte danach zu `failed`; das Video blieb bei `readyState 0`. Deshalb gibt es aus diesem Lauf **keinen ehrlichen FPS-Wert**. Die wichtigste Performance-Empfehlung ist nicht weiteres UI-Tuning auf Basis erfundener FPS, sondern zuerst direkte Tailscale-Konnektivität und UDP/ICE zu reparieren und danach Frame- sowie Touch-to-Pixel-Messung erneut auszuführen.

### Einordnung der Streaming-Stapel nach dem r56-Nachtest

| Stack | frischer lokaler Stand | Entscheidung |
|---|---|---|
| KasmVNC 1.3.3 + noVNC 1.4.x | vollständiger aktueller Produktpfad, 291 Mobile-/Access-Checks; erster nichtschwarzer Frame Median 180 ms | beibehalten |
| Selkies | 20/20 HTTP und WebSocket; erster nichtschwarzer Frame Median 5.272 ms nach langsamen Reloads; kein gleichwertiger Produkt-E2E | Reload-/Sessionproblem untersuchen, noch nicht migrieren |
| Sunshine/Moonlight | `architecture_only`, keine aktuelle Messung | nicht als Latenzvergleich werten |
| Apache Guacamole 1.6 | 20/20 HTTP; erster nichtschwarzer Frame Median 351 ms; zusätzliche Gateway- und fehlende Policy-/CDP-Integration | kein Web-MVP-Core |

Frühere isolierte KasmVNC-1.4- und Selkies-POCs sowie der neue Selkies-Readiness-Lauf sind in `docs/REMOTE-STREAMING-BENCHMARK.md` methodisch getrennt dokumentiert. Einen technologieübergreifenden „Latenz-Sieger“ zu behaupten wäre weiterhin falsch. KasmVNC ist die aktuelle Produktempfehlung, weil nur dieser Pfad vollständig integriert und aktuell end-to-end geprüft ist.

## Empfohlene Chrome-/Profilbasis für Mobile

1. **1024 × 576 als verifizierten Standard behalten.** Genau diese laufende Profilauflösung bestand die frischen Mobile-, Auth- und warmen Loopback-Gates; der r49-Lauf enthält keinen neuen 1440-×-900-Vergleich.
2. **Pane und noVNC-Zoom für spontane Anpassungen nutzen.** Sie reagieren live und halten den Remote-Browserprozess stabil.
3. **390 × 844 oder Phone-fit bewusst pro Profil anwenden**, wenn die Zielseite ihre echte Mobile-Responsive-Variante liefern soll; die UI startet ein laufendes Profil dafür kontrolliert neu und verbindet den Canvas anschließend wieder.
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
- Der r50-VCVM/Neko-Lauf belegt Tailnet-HTTP und geschützten Login über Codex Computer Use, aber wegen fehlgeschlagenem WebRTC-ICE keine Neko-Framerate. Für KasmVNC existiert nur der oben klar begrenzte Canvas-Änderungsproxy.
- Kein echter Mobilfunk- und kein Touch-to-Pixel-p50/p95-Bericht.
- Der Grid-View ist ein schneller Session-/Profilumschalter, kein gleichzeitiges Multi-Canvas-Monitoring.
- Der Chat-Composer akzeptiert in r56 ausschließlich den verifizierten Codex-Computer-Use-Providervertrag, benötigt aber weiterhin einen realen Host-Bridge-Prozess und ist keine eigenständige Vendor-API.
- Selkies und Guacamole sind reproduzierbar provisioniert und browserbeobachtet, aber weiterhin keine gleichwertigen authentifizierten Produktdeployments; Sunshine/Moonlight bleibt ein Architekturpfad.

## Freigabeempfehlung

Der aktuelle Stand ist als **VCVM-gehostetes, rollenbasiertes Mobile-Web-MVP** freigabefähig. Für eine direkte iPhone-Freigabe fehlen noch Tailscale Serve/HTTPS, ein physisches Safari-Gerät sowie echte Touch-to-Pixel- und Reconnect-Messungen. Die höchste nächste Produktpriorität ist die direkte Tailnet-Verbindung, danach iOS-IME-Bridge, Keyboard-Zubehörleiste und echte Eingabelatenztelemetrie. Die UI/UX-Details stehen im [Mobile UI/UX and universal browser-action audit](MOBILE-UI-UX-HARNESS-AUDIT-2026-07-21.md).
