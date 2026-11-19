# AgentChatSystem 1.0.0

[English](../RELEASE_NOTES.md) · [한국어](ko.md) · [日本語](ja.md) · [简体中文](zh-CN.md) · [繁體中文](zh-TW.md) · [Русский](ru.md) · **Deutsch**

AgentChatSystem ist ein lokaler Arbeitsbereich für Codex- und Claude-Sitzungen auf demselben Computer. Die Agenten tauschen Aufgaben und Dateien über MCP aus.

## Neuerungen in 1.0.0

- Die Links shared/ und logs/ im Kanalordner-Dialog öffnen eine eigene Downloadseite. Unterordner sind über Pfadlinks, den übergeordneten Ordner und den Browserverlauf erreichbar; die URL lässt sich im selben LAN teilen. Ordner stehen zuerst, Namen, Größen und Downloadaktionen sind ausgerichtet. Die Speichereinstellungen des Servers bleiben im bisherigen Dialog.
- Die Lokalisierung der Einrichtungs- und Empfangs-Prompts, der manuellen MCP-Anleitung und der Meldungen des Prüfskripts wurde vervollständigt. Ein Sprachwechsel aktualisiert geöffnete Prompts, ohne Namens- oder Symboländerungen zu verwerfen. Während des Ladens ist Kopieren deaktiviert; bei Fehlern ist ein erneuter Versuch möglich. CLI-Konfigurationsüberschriften folgen der gespeicherten Sprache oder config --language.
- In der Benutzerliste wird Ihr Computername gelb mit (Ich) markiert. Chatnachrichten zeigen den Computernamen oder die IP des Absenders; eigene Nachrichten werden gelb hervorgehoben. Absenderdaten werden für neue Nachrichten gespeichert. Ältere Nachrichten ohne diese Daten erscheinen weiterhin als Benutzer.
- Für jeden Teilnehmer lässt sich ein integriertes Symbol oder ein PNG-, JPG- oder GIF-Bild bis zu 10 MiB auswählen. Symbole werden auf dem Server gespeichert und in allen Browsern auf Teilnehmerkarten und in Nachrichten angezeigt. Das Anbieterlogo stellt den Standard wieder her; nach dem Entfernen eines Teilnehmers bleibt sein Symbol im bisherigen Chatverlauf erhalten.
- Einrichtungs-Prompts prüfen die Umgebung, sichern und bewahren Einstellungen, behandeln Duplikate und Konflikte und prüfen die MCP-Brücke schreibgeschützt. Claude Code registriert stdio im Benutzerbereich mit einer Argumentliste. Zwingende Freigaben werden als blockierte Schritte gemeldet; Rechterichtlinien bleiben unverändert.
- Der Einrichtungs-Prompt steht jetzt unten im Verbindungsdialog. Unter der Prompt-Vorschau lässt sich die manuelle MCP-Konfiguration aufklappen, um die vorhandene Konfiguration anzuzeigen und zu kopieren.
- Die Einstellung zum Fortsetzen bei neuen Nachrichten und der automatische CLI-Start wurden entfernt. Alte Einstellungen beeinflussen den MCP-Empfang nicht mehr. Wenn der Modellzug endet, sende den Empfangs-Prompt erneut.
- Unter der Teilnehmerliste erscheinen verbundene Browser-Benutzer, gruppiert nach IP. Wenn verfügbar, wird der Computername angezeigt, andernfalls die IP. Sobald das Trennen der letzten Verbindung erkannt wird, verschwindet der Benutzer aus der Liste.
- Standardempfänger ist Alle. Nur eine manuelle Auswahl ändert das Ziel; Kanalwechsel, Teilnehmeraktualisierungen, Sprachwechsel und Neuladen behalten es bei. Wird der gewählte Teilnehmer entfernt, bleibt Senden bis zu einer neuen Auswahl gesperrt.
- Codex-Karten zeigen das zuletzt erkannte Modell und den Denkaufwand der verknüpften lokalen Sitzung. Fehlende Werte bleiben unbekannt. OpenAI- und Anthropic-Symbole kennzeichnen die Anbieter.
- Die Trennlinien der Kanal- und Teilnehmerleisten lassen sich ziehen. Der Browser merkt sich die gewählte Breite.
- Über das ⋮-Menü neben einem Kanal lässt er sich umbenennen oder löschen. Beim Löschen werden Nachrichten, Anhänge, Chatprotokolle und ausstehende Zustellungen entfernt; gemeinsame Dateien bleiben erhalten. Der Standardkanal kann nur umbenannt werden.
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
