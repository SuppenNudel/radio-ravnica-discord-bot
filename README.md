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

Technisch nutzt der Bot dafuer den oeffentlichen YouTube-RSS-Feed des Kanals. Wenn YouTube den Feed mit HTTP 404 ablehnt, liest der Bot als Fallback die strukturierten Daten der oeffentlichen Videos-Seite. Optional kann die Kanal-ID direkt ueber `YOUTUBE_CHANNEL_GAMERII_ID` gesetzt werden; wenn sie fehlt, loest der Bot sie einmalig ueber die oeffentliche `@Handle`-Seite auf.

Nur die YouTube-Cog-Tests laufen mit:

```bash
./venv/bin/python -m unittest tests.test_youtube_cog
```