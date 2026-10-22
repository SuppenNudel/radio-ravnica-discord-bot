# radio-ravnica-discord-bot

## Feature Overview

- Turnier-Management in Discord mit `/erstelle_turnier` (Teilnahme, Pairings, Ergebnis-Meldung, Standings)
- Paper-Event-Einreichung mit `/poste_turnier` inkl. Datenbank-Export und Post in 📅paper-turniere
- YouTube-Benachrichtigungen für neue GameRii-Videos
- Ask-Us-Anything Einträge in Datenbank zur Nachverfolgung offener Fragen
- Erinnerungen per Kontextmenü und Slash-Command `/erinnere_mich`
- Arena Daily Deals Monitoring mit automatischem Post in 🏟mtg-arena

- Detaillierte Beschreibung: [documentation/features.md](documentation/features.md)

## Changelog

- Siehe [CHANGELOG.md](CHANGELOG.md) für die Versionshistorie.

## Ideen

### Tournament Management
    - für die Webcam Turniere
    - Anmeldung
    - Ergebnisse melden

### Events Posten
    - für #paper-events

### Konversation verschieben
    - wenn eine Koversation in einen anderen Kanal verschoben werden soll

### Youtube Benachrichtigung
    - wenn Robin ein neues Video hochgeladen hat

Technisch nutzt der Bot dafür den öffentlichen YouTube-RSS-Feed des Kanals. Wenn YouTube den Feed mit HTTP 404 ablehnt, liest der Bot als Fallback die strukturierten Daten der öffentlichen Videos-Seite. Optional kann die Kanal-ID direkt über `YOUTUBE_CHANNEL_GAMERII_ID` gesetzt werden; wenn sie fehlt, löst der Bot sie einmalig über die öffentliche `@Handle`-Seite auf.

Bereits bekannte Video-IDs werden pro Discord-Zielkanal und YouTube-Handle dauerhaft in `data/youtube_feed.db` gespeichert, damit wechselnde Feed-Inhalte nach einem Neustart keine erneuten Benachrichtigungen auslösen. Beim ersten Start ohne gespeicherten Verlauf wird der aktuelle Feed nur als Ausgangsstand erfasst und nicht gepostet. Bei späteren Neustarts werden neue Videos seit dem letzten Lauf nachgeholt, soweit sie noch im abgefragten Feed enthalten sind. Die Datenbank muss bei Deployments erhalten bleiben; bestehende Discord-Nachrichten werden nicht nachträglich eingelesen.

Nur die YouTube-Cog-Tests laufen mit:

```bash
./venv/bin/python -m unittest tests.test_youtube_cog
```