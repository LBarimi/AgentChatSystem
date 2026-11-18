# AgentChatSystem 1.0.0

[English](../RELEASE_NOTES.md) · [한국어](ko.md) · [日本語](ja.md) · [简体中文](zh-CN.md) · [繁體中文](zh-TW.md) · [Русский](ru.md) · **Deutsch**

AgentChatSystem ist ein lokaler Arbeitsbereich für Codex- und Claude-Sitzungen auf demselben Computer. Die Agenten tauschen Aufgaben und Dateien über MCP aus.

## Neuerungen in 1.0.0

- Unter Windows startest du mit `bin/start.cmd`. Unter Linux und macOS führst du `sh bin/start.sh` im Projektordner aus.
- Der Server lauscht ausschließlich auf `127.0.0.1`. Der Standardport ist `9081` und lässt sich in `bin/settings.ini` ändern.
- Die Farbpaletten für Hell, Dunkel und Graphit wurden überarbeitet. Ozean, Sand und Pflaume sind neu. Das Systemdesign folgt dem hellen oder dunklen Modus des Browsers.
- Die Oberfläche sowie der Einrichtungs- und der Empfangs-Prompt sind auf Englisch, Koreanisch, Japanisch, vereinfachtem und traditionellem Chinesisch, Russisch und Deutsch verfügbar. Beim ersten Aufruf wählt die Anwendung eine unterstützte Sprache aus den Browsereinstellungen und speichert sie in `bin/settings.ini`.

## Funktionen

- Registriere, benenne und entferne mehrere Codex- und Claude-Teilnehmer.
- Erstelle Kanäle und sende Nachrichten an einzelne Agenten oder an alle. Lege für jeden Kanal einen Speicherort für Freigabedateien und Protokolle fest.
- Veröffentliche Anweisungen und Bilder auf der Pinnwand und prüfe, welche Agenten einen Beitrag gelesen haben. Beim Löschen eines Beitrags erhalten die Agenten eine Benachrichtigung.
- Hänge Markdown- und PNG-Dateien an Chatnachrichten an. PNG-Antworten von Agenten lassen sich ansehen und herunterladen. Ältere MCP-Verbindungen können PNG-Dateien über eine Anweisung im Antworttext anhängen.
- Ein Empfangsaufruf wartet bis zu 40 Sekunden und kehrt sofort zurück, sobald eine Nachricht eintrifft.
- Mit Enter sendest du eine Nachricht und mit Shift+Enter fügst du einen Zeilenumbruch ein. Das Eingabefeld berücksichtigt die koreanische Eingabemethode.
- Leere den Chatverlauf und ausstehende Zustellungen eines Kanals. Kopiere die MCP-Konfiguration sowie Einrichtungs- und Empfangs-Prompt aus der Teilnehmerkarte.

## Starten

Installiere Python 3.10 oder neuer. Füge unter Windows `python` zu PATH hinzu und starte [`bin/start.cmd`](../bin/start.cmd). Füge unter Linux und macOS `python3` zu PATH hinzu, führe `sh bin/start.sh` aus und öffne die ausgegebene Adresse im Browser. Die Standardadresse ist [http://127.0.0.1:9081/](http://127.0.0.1:9081/). Für einen anderen Port bearbeite [`bin/settings.ini`](../bin/settings.ini) und starte den Server neu. Die Schritte zum Verbinden stehen in der [README](../README.md).

Der Server und die MCP-Bridge verwenden die Python-Standardbibliothek und laufen ohne API-Schlüssel. KI-Client, Konto, Modellzugriff und Werkzeuge zur Bilderzeugung stellst du in der jeweiligen Sitzung bereit.

Falls eine vorhandene MCP-Konfiguration auf `bin/local_chat.pyz` verweist, kopiere die aktuelle Konfiguration aus der Teilnehmerkarte und verbinde den Client erneut.

## Aktueller Funktionsumfang

- Agenten rufen normalerweise wiederholt MCP `receive` auf. Wenn der Modellzug endet, sende den Empfangs-Prompt erneut, um den Empfang fortzusetzen.
- **Bei neuen Nachrichten fortsetzen** ist experimentell und standardmäßig deaktiviert. Die Funktion setzt die offizielle CLI und ein angemeldetes Konto voraus. Teste sie in einer eigenen CLI-Sitzung, da eine Desktop-App die Sitzung belegen kann.
- Wiederholte Empfangsaufrufe und die eigentliche Arbeit können auf die Nutzung des verbundenen KI-Dienstes angerechnet werden.
- Unter Linux und macOS beendest du die Anwendung mit Strg+C im Server-Terminal.

## Inhalt des ZIP-Archivs

`AgentChatSystem-v1.0.0.zip` enthält den Quellcode, Startskripte für Windows, Linux und macOS, `bin/internal/AgentChatSystem.pyz`, Standardeinstellungen, die README, Versionshinweise in sieben Sprachen, die [MIT-Lizenz des Projekts](../LICENSE), Lizenzhinweise für die mitgelieferten Oberflächenressourcen und Dateiprüfsummen.

Beim Packen werden lokale Chat- und Pinnwanddatenbanken, Uploads, gemeinsame Ordner, Protokolle, persönliche MCP-Einstellungen, Git-Verlauf, temporäre Dateien und projektfremde Dateien ausgelassen. Beim ersten Start legt die Anwendung das lokale Verzeichnis `var/` an.
