# radio-ravnica-discord-bot

## Feature Overview

- Turnier-Management in Discord mit `/erstelle_turnier` (Teilnahme, Pairings, Ergebnis-Meldung, Standings)
- Paper-Event-Einreichung mit `/poste_turnier` inkl. Datenbank-Export und Post in 📅paper-turniere
- YouTube-Benachrichtigungen für neue GameRii-Videos
- Ask-Us-Anything Einträge in Datenbank zur Nachverfolgung offener Fragen
- Erinnerungen per Kontextmenü und Slash-Command `/erinnere_mich`
- Arena Daily Deals Monitoring mit automatischem Post in 🏟mtg-arena

- Detaillierte Beschreibung: [documentation/features.md](documentation/features.md)

## Liga-Turniere und Elo-Wertung

Beim Erstellen eines Turniers kann die Organisation zwischen einem Swiss-Turnier und einer Liga wählen. In einer Liga gibt es keine vorgegebenen Paarungen: Aktive Teilnehmer wählen verfügbare Gegner selbst und melden die Spielergebnisse über **Ergebnis melden**. Weitere Teilnehmer können auch nach dem Ligastart beitreten. Teilnehmerlimit, Warteliste und Turniersperren gelten weiterhin. Die Matchhistorie eines Spielers bleibt beim Aus- und späteren Wiedereintritt erhalten.

### Wertungssystem wählen

Im Dialog **Rematch-Regeln** kann vor dem Ligastart das Wertungssystem festgelegt werden:

- **Matchpunkte** ist die Voreinstellung. Ein Matchsieg gibt 3 Punkte, ein Unentschieden 1 Punkt. Die Rangliste verwendet zusätzlich die bestehenden Swiss-Tiebreaker: Gegner-Match-Win-Percentage, Game-Win-Percentage und Gegner-Game-Win-Percentage.
- **Elo-System** startet alle Spieler bei 1000 Punkten und verwendet einen K-Faktor von 32. Berechnet wird die Erwartung für Spieler A gegenüber Spieler B aus deren Ratings:

    $$E_A = \frac{1}{1 + 10^{(R_B - R_A)/400}}$$

    Das Match-Ergebnis ist 1 für einen Sieg, 0,5 für ein Unentschieden und 0 für eine Niederlage. Beide Ratings werden gleichzeitig aktualisiert:

    $$R'_A = R_A + 32(S_A - E_A), \qquad R'_B = R_B + 32(S_B - E_B)$$

    Dabei ist $S$ das tatsächliche Ergebnis und $E$ die erwartete Punktzahl. Die Anzahl einzelner Spiele im Match beeinflusst Elo nicht; ausschlaggebend ist das Match-Ergebnis. Elo-Ranglisten sortieren nach Rating.

### Elo-Soft-Reset

Für Elo kann die Organisation ein Reset-Intervall in Tagen festlegen; voreingestellt sind 30 Tage. Bei jedem fälligen Reset wird der Abstand jedes Ratings zum Startwert 1000 halbiert. Beispiele: 1200 wird zu 1100, 800 zu 900. Mehrere verpasste Intervalle, etwa während der Bot offline war, werden beim nächsten Lauf nacheinander angewendet. Mit `0` ist der Soft-Reset deaktiviert. Das Intervall und der Zeitpunkt des letzten Resets werden gespeichert und nach einem Neustart fortgesetzt.

### Rematch-Cooldown und Laufzeit

Die Rematch-Regeln legen außerdem fest, wie viele Matches ein Paar spielen darf, bevor beide ihren Cooldown erfüllen müssen, welcher Prozentsatz der anderen aktiven Spieler dafür erforderlich ist und ob wiederholte Matches gegen denselben anderen Gegner mehrfach zählen. Die Vorgaben werden beim Start der Liga fixiert. Neue aktive Teilnehmer fließen in die Cooldown-Berechnung ein; ausgeschiedene Spieler nicht.

Eine Liga benötigt eine Startzeit. Ein Enddatum ist optional. Ist es gesetzt, muss es nach dem Start liegen; zum Endzeitpunkt werden weitere Ergebnismeldungen geschlossen. Ohne Enddatum bleibt die Liga offen, bis sie von der Organisation beendet oder abgebrochen wird. Ein Elo-Soft-Reset läuft auch in einer offenen Liga weiter.

Ligaeinstellungen, Matchhistorie, Elo-Ratings und Resetzeitpunkt werden in `data/tournaments.db` gespeichert. Die Datenbankmigration ergänzt fehlende Spalten beim Botstart; bestehende Turniere bleiben Swiss und neue Ligen verwenden zunächst Matchpunkte.

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