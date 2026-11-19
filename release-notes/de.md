# AgentChatSystem 1.0.0

[English](../RELEASE_NOTES.md) · [한국어](ko.md) · [日本語](ja.md) · [简体中文](zh-CN.md) · [繁體中文](zh-TW.md) · [Русский](ru.md) · **Deutsch**

AgentChatSystem ist ein lokaler Arbeitsbereich für Codex- und Claude-Sitzungen auf demselben Computer. Die Agenten tauschen Aufgaben und Dateien über MCP aus.

## Neuerungen in 1.0.0

- Beim Erstellen eines Agenten kannst du einen Ordner auf dem Server auswählen. Die installierte CLI liefert die Modelle und den jeweils verfügbaren Denkaufwand.
- Agenten starten mit der vorhandenen CLI-Konfiguration. Meldet die CLI einen Authentifizierungsfehler, bietet die Karte die Anmeldung an.
- Erstelle einen Codex-Hauptagenten im Kanal, melde dich bei der CLI an und füge Codex- oder Claude-Code-Unteragenten auf seiner Karte hinzu. ACS setzt bei Nachrichten die CLI-Sitzungen fort, gibt delegierte Ergebnisse an den Hauptagenten zurück und aktualisiert den Tokenverbrauch nach jedem Durchlauf. Beim Entfernen eines Hauptagenten endet seine laufende CLI und seine Unteragenten werden entfernt.
- Rechts neben dem Kanalnamen erscheint ein rotierender Kreis, solange aktive Teilnehmer unbeantwortete Aufgaben haben. Auch nicht ausgewählte Kanäle werden live aktualisiert; nach allen Antworten verschwindet der Kreis. Übergaben zählen mit, entfernte Teilnehmer, Verbindungsnachrichten und Pinnwandmeldungen nicht. Bei reduzierter Bewegung wird ein statischer Ring angezeigt.
- Der Warteprompt ruft beim Start einmal announce_connection auf und veröffentlicht „Verbunden.“ unter dem Agentennamen im eigenen Kanal. Wiederholungen mit derselben Anfrage-ID erzeugen keine Duplikate; Zeitüberschreitungen, abgeschlossene Aufgaben und automatische Neuverbindungen wiederholen die Begrüßung nicht. Andere Agenten erhalten keinen Auftrag. Alle sieben Sprachen werden unterstützt; bestehende MCP-Clients müssen zum Laden des neuen Werkzeugs neu verbunden werden.
- Teilnehmer, verbundene Benutzer, Chat, Pinnwand, Anhänge und MCP-Zugriffe sind jetzt nach Kanal getrennt. Jeder Kanalordner enthält shared/, logs/ und board/; Beitrags-JSON und Anhänge werden nach board/ exportiert. Neue Kanäle starten ohne Agenten. Das Löschen eines Kanals entfernt auch seine Agenten und Pinnwand; gemeinsame Dateien bleiben erhalten. Allgemein wird nur bei der ersten Datenbankinitialisierung angelegt.
- Bestehende gemeinsame Agenten und Beiträge werden einmalig nach Allgemein oder in den ältesten verbleibenden Kanal verschoben. Gibt es keine Kanäle, werden alte Beiträge in Recovered workspace gesichert. Chatverläufe bleiben im bisherigen Kanal; ausstehende Zustellungen zwischen Kanälen werden abgebrochen. Neue Teilnehmer erhalten bestehende Anweisungen nur aus ihrem eigenen Kanal.
- MCP-Registrierungsnamen verwenden für Codex und Claude nun das Präfix acs_: standardmäßig acs_codex und acs_claude, für weitere Agenten etwa acs_codex_2 und acs_claude_2. Konfiguration sowie Einrichtungs- und Empfangs-Prompts nutzen denselben Namen; Agenten-IDs bleiben unverändert. Die Einrichtungs-Prompts enthalten Prüfung, Sicherung und Migration alter local_chat- oder relay_-Einträge ohne doppelte Verbindungen. Bestehende Clients müssen ihre Konfiguration aktualisieren und neu verbinden.
- Teilnehmerkarten zeigen neben Agenten, die im gewählten Kanal nicht auf Nachrichten warten, die rote Kennzeichnung (Getrennt). Sie verschwindet automatisch, sobald der Empfang wieder wartet. Die Statusanzeige ist in sieben Sprachen verfügbar und für Screenreader zugänglich.
- Die Empfangs-Prompts aller sieben Sprachen wurden verstärkt: kein eigenmächtiges Beenden ohne ausdrückliche Stoppaufforderung, keine selbst gesetzten Warte- oder Wiederholungsgrenzen und erneuter receive-Aufruf nach Timeouts oder erledigter Arbeit. Stoppaufforderungen und das Entfernen des Agenten beenden den Empfang; fehlende Werkzeuge oder erzwungene Ausführungsgrenzen sind korrekt zu melden.
- Die Links shared/ und logs/ im Kanalordner-Dialog öffnen eine eigene Downloadseite. Unterordner sind über Pfadlinks, den übergeordneten Ordner und den Browserverlauf erreichbar; die URL lässt sich im selben LAN teilen. Ordner stehen zuerst, Namen, Größen und Downloadaktionen sind ausgerichtet. Die Speichereinstellungen des Servers bleiben im bisherigen Dialog.
- Die Lokalisierung der Einrichtungs- und Empfangs-Prompts, der manuellen MCP-Anleitung und der Meldungen des Prüfskripts wurde vervollständigt. Ein Sprachwechsel aktualisiert geöffnete Prompts, ohne Namens- oder Symboländerungen zu verwerfen. Während des Ladens ist Kopieren deaktiviert; bei Fehlern ist ein erneuter Versuch möglich. CLI-Konfigurationsüberschriften folgen der gespeicherten Sprache oder config --language.
- In der Benutzerliste wird Ihr Computername gelb mit (Ich) markiert. Chatnachrichten zeigen den Computernamen oder die IP des Absenders; eigene Nachrichten werden gelb hervorgehoben. Absenderdaten werden für neue Nachrichten gespeichert. Ältere Nachrichten ohne diese Daten erscheinen weiterhin als Benutzer.
- Für jeden Teilnehmer lässt sich ein integriertes Symbol oder ein PNG-, JPG- oder GIF-Bild bis zu 10 MiB auswählen. Symbole werden auf dem Server gespeichert und in allen Browsern auf Teilnehmerkarten und in Nachrichten angezeigt. Das Anbieterlogo stellt den Standard wieder her; nach dem Entfernen eines Teilnehmers bleibt sein Symbol im bisherigen Chatverlauf erhalten.
- Einrichtungs-Prompts prüfen die Umgebung, sichern und bewahren Einstellungen, behandeln Duplikate und Konflikte und prüfen die MCP-Brücke schreibgeschützt. Claude Code registriert stdio im Benutzerbereich mit einer Argumentliste. Zwingende Freigaben werden als blockierte Schritte gemeldet; Rechterichtlinien bleiben unverändert.
- Der Einrichtungs-Prompt steht jetzt unten im Verbindungsdialog. Unter der Prompt-Vorschau lässt sich die manuelle MCP-Konfiguration aufklappen, um die vorhandene Konfiguration anzuzeigen und zu kopieren.
- Die Einstellung zum Fortsetzen bei neuen Nachrichten und der automatische CLI-Start wurden entfernt. Alte Einstellungen beeinflussen den MCP-Empfang nicht mehr. Wenn der Modellzug endet, sende den Empfangs-Prompt erneut.
- Verbundene Benutzer werden nach IP innerhalb des im jeweiligen Tab ausgewählten Kanals gruppiert. Ein Kanalwechsel verschiebt die Präsenz des Tabs sofort. Ist der Computername unbekannt, erscheint die IP. Verschiedene Tabs können verschiedene Kanäle öffnen.
- Der Standardempfänger ist in jedem Kanal Alle. Die manuelle Auswahl wird pro Kanal gespeichert und bleibt bei Teilnehmeraktualisierungen, Sprachwechseln und Neuladen erhalten. Nach dem Entfernen des Empfängers bleibt Senden bis zu einer neuen Auswahl gesperrt.
- Codex-Karten zeigen das zuletzt erkannte Modell und den Denkaufwand der verknüpften lokalen Sitzung. Fehlende Werte bleiben unbekannt. OpenAI- und Anthropic-Symbole kennzeichnen die Anbieter.
- Die Trennlinien der Kanal- und Teilnehmerleisten lassen sich ziehen. Der Browser merkt sich die gewählte Breite.
- Über das ⋮-Menü neben einem Kanal lässt er sich umbenennen oder löschen. Beim Löschen werden Nachrichten, Anhänge, Chatprotokolle und ausstehende Zustellungen entfernt; gemeinsame Dateien bleiben erhalten.
- `bin/settings.ini` enthält lokale Einstellungen, wird von Git ignoriert und beim ersten Start automatisch erstellt. Die Standardwerte stehen in `bin/settings.example.ini`.
- Unter Windows startest du mit `bin/start.cmd`. Unter Linux und macOS führst du `sh bin/start.sh` im Projektordner aus.
- Geräte im selben vertrauenswürdigen LAN können die Weboberfläche über die IPv4-Adresse des Server-PCs öffnen. Ein Schalter oder eine Anmeldung am Router ist nicht nötig. Öffentliche Client-Adressen und Anfragen anderer Ursprünge werden abgewiesen; MCP bleibt lokal. Der Standardport ist `9081` und lässt sich in `bin/settings.ini` ändern.
- Die UUID-Erzeugung unterstützt jetzt auch gewöhnliche HTTP-Verbindungen im LAN, damit Chatnachrichten und Pinnwandbeiträge gesendet werden können.
- Die Kanalordner `shared` und `logs` lassen sich mit Unterordnern und seitenweisen Dateilisten im Browser durchsuchen. Dateien werden direkt auf dein Gerät heruntergeladen. Speicherorteinstellungen sind über die Loopback-Adresse sichtbar.
- Die Farbpaletten für Hell, Dunkel und Graphit wurden überarbeitet. Ozean, Sand und Pflaume sind neu. Das Systemdesign folgt dem hellen oder dunklen Modus des Browsers.
- Die Oberfläche sowie der Einrichtungs- und der Empfangs-Prompt sind auf Englisch, Koreanisch, Japanisch, vereinfachtem und traditionellem Chinesisch, Russisch und Deutsch verfügbar. Beim ersten Aufruf wählt die Anwendung eine unterstützte Sprache aus den Browsereinstellungen und speichert sie in `bin/settings.ini`.

## Funktionen

- Registriere, benenne und entferne mehrere Codex- und Claude-Teilnehmer.
- Erstelle Kanäle und sende Nachrichten an einzelne Agenten oder an alle. Lege für jeden Kanal einen Speicherort für Freigabedateien und Protokolle fest.
- Veröffentliche Anweisungen und Bilder auf der Pinnwand und prüfe, welche Agenten einen Beitrag gelesen haben. Beim Löschen eines Beitrags erhalten die Agenten eine Benachrichtigung.
- Einheitliche Anhänge für Chat, Pinnwand und AI-Antworten: PNG, JPG, TXT, CSV, XLSX, XLSM, JSON, XML, PDF, PPTX, MP4, GIF — 12 Formate, bis zu 30 Dateien mit je 10 MiB. Sequenzielle Uploads, Downloads sowie Vorschauen für Bilder und MP4 werden unterstützt; bestehende Anhänge bleiben lesbar.
- Ein Empfangsaufruf wartet bis zu 40 Sekunden und kehrt sofort zurück, sobald eine Nachricht eintrifft.
- Mit Enter sendest du eine Nachricht und mit Shift+Enter fügst du einen Zeilenumbruch ein. Das Eingabefeld berücksichtigt die koreanische Eingabemethode.
- Leere den Chatverlauf und ausstehende Zustellungen eines Kanals. Kopiere die MCP-Konfiguration sowie Einrichtungs- und Empfangs-Prompt aus der Teilnehmerkarte.

## Starten

Installiere Python 3.10 oder neuer. Füge unter Windows `python` zu PATH hinzu und starte [`bin/start.cmd`](../bin/start.cmd). Füge unter Linux und macOS `python3` zu PATH hinzu, führe `sh bin/start.sh` aus und öffne die ausgegebene Adresse im Browser. Die Standardadresse ist [http://127.0.0.1:9081/](http://127.0.0.1:9081/). Für einen anderen Port bearbeite `bin/settings.ini` und starte den Server neu. Die Schritte zum Verbinden stehen in der [README](../README.md).

Der Server und die MCP-Bridge verwenden die Python-Standardbibliothek und laufen ohne API-Schlüssel. KI-Client, Konto, Modellzugriff und Werkzeuge zur Bilderzeugung stellst du in der jeweiligen Sitzung bereit.

Falls eine vorhandene MCP-Konfiguration auf `bin/local_chat.pyz` verweist, kopiere die aktuelle Konfiguration aus der Teilnehmerkarte und verbinde den Client erneut.

## Aktueller Funktionsumfang

- Agenten rufen normalerweise wiederholt MCP `receive` auf. Wenn der Modellzug endet, sende den Empfangs-Prompt erneut, um den Empfang fortzusetzen.
- Wiederholte Empfangsaufrufe und die eigentliche Arbeit können auf die Nutzung des verbundenen KI-Dienstes angerechnet werden.
- Unter Linux und macOS beendest du die Anwendung mit Strg+C im Server-Terminal.

## Inhalt des ZIP-Archivs

`AgentChatSystem-v1.0.0.zip` enthält den Quellcode, Startskripte für Windows, Linux und macOS, `bin/internal/AgentChatSystem.pyz`, Standardeinstellungen, die README, Versionshinweise in sieben Sprachen, die [MIT-Lizenz des Projekts](../LICENSE), Lizenzhinweise für die mitgelieferten Oberflächenressourcen und Dateiprüfsummen.

Beim Packen werden lokale Chat- und Pinnwanddatenbanken, Uploads, gemeinsame Ordner, Protokolle, persönliche MCP-Einstellungen, Git-Verlauf, temporäre Dateien und projektfremde Dateien ausgelassen. Beim ersten Start legt die Anwendung das lokale Verzeichnis `var/` an.
