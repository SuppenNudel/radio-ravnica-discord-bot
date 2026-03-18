# Changelog

## 26. September 2025

### Geändert

- Der Status des Bots zeigt, wie viele Turniere aktuell laufen.

## 25. September 2025

### Hinzugefügt

- Beim Übermitteln von Turnierergebnissen kann nun eine Nachricht an den Gegner hinterlassen werden.

## 2. September 2025

### Behoben

- MTGTop8-Links werden nun auch mit `www.` am Anfang erkannt.

## 10. April 2025

### Geändert

- Anpassung des Vorgangs für die **📅paper-turniere**-Einreichung.
- Posts können nun vom Author nachträglich bearbeitet werden.
- Das betrifft auch die Posts, die bereits gemacht wurden.
- Hier eine kleine Demonstration:
  - https://www.youtube.com/watch?v=8Xzmw53KXm8

## 1. April 2025

### Hinzugefügt

- Alpha-Veröffentlichung der Turnier-Software. Befehl: `/erstelle_turnier`

## 25. März 2025

### Geändert

- Arena Daily Deals werden nicht mehr als Bluesky-Link gepostet, sondern direkt die Bilder des Stores (aus dem Bluesky-Post).

## 13. Februar 2025

### Behoben

- `/poste_turnier`
  - Bei Fehlern wird eine Rückmeldung an den Anwender gegeben (wird diesem vermutlich nicht viel sagen, aber dem Entwickler 😉).
  - Der Bot reagiert sofort nach einer Anfrage zumindest mit „…, Augenblick ⏳“, damit man nicht von einem Absturz ausgeht.

## 12. Februar 2025

### Geändert

- `/poste_turnier`
  - Für Event Typ gibt es nun auch eine Auswahlliste, statt Freitext angeben zu müssen.

### Behoben

- `/poste_turnier`
  - Format(e) und Event Typ werden jetzt auch in die Datenbank geschrieben.

## 12. Februar 2025

### Geändert

- `/poste_turnier`
  - Für Format(e) wird nun eine Auswahlliste angeboten, anstatt dass man Freitext eingeben muss.

## 11. Februar 2025

### Hinzugefügt

- `/poste_turnier` Befehl
  - <Erklärung folgt>

## 7. Februar 2025

### Hinzugefügt

- Arena Daily Deals: Der Bot prüft alle 15 Minuten, ob Arena Daily Deals auf Bluesky einen neuen Post gemacht hat. Ist das der Fall, wird der Link zum Post in 🏟mtg-arena gesendet.

## 31. Januar 2025

### Hinzugefügt

- Bei der `remind-me`-Funktion wird nun nach einer Bestätigung gebeten, ob das interpretierte Datum auch das Gewollte ist.
- Schlägt die Datumserkennung durch `dateparser` fehl, wird es mit `gemini-1.5-flash` versucht. Für die Transparenz, der Prompt ist: `Jetzt ist {now}. Welches Datum und Uhrzeit ist {user_time_input}? Prüfe das Ergebnis nochmal nach! Gib mir nur das Datum mit Uhrzeit.`

## 27. Januar 2025

### Hinzugefügt

- `remind-me`-Funktion nun auch als Slash Command: **/erinnere_mich**

### Geändert

- Schlägt die Datumserkennung fehl, kann man die Eingabe noch einmal versuchen.

## 25. Januar 2025

### Behoben

- Fehler im Titel von **[📅paper-turniere](https://discord.com/channels/783441128119730236/1207651082491142154)**-Einträgen.

### Geändert

- Verwendet nun eine andere Bibliothek, um Zeiteinträge für die `remind-me`-Funktion zu parsen. Sollte nun intuitiver sein.

## 23. Januar 2025

### Hinzugefügt

- **/events_in_meiner_nähe** Prototyp für einen Befehl, der einem alle eingereichten **[📅paper-turniere](https://discord.com/channels/783441128119730236/1207651082491142154)** nach Entfernung sortiert als Privatnachricht schickt. Der Link führt zu den jeweiligen **[📅paper-turniere](https://discord.com/channels/783441128119730236/1207651082491142154)**-Foreneinträgen.

![Prototyp /events_in_meiner_nähe](documentation/images/events-in-meiner-naehe-prototyp.png)

## 22. Januar 2025

### Hinzugefügt

- `.ics`-Dateien sind zurück für neue Posts in **[📅paper-turniere](https://discord.com/channels/783441128119730236/1207651082491142154)**.

## 18. Januar 2025

### Hinzugefügt

- Möglichkeit, eigenes Bild für **[📅paper-turniere](https://discord.com/channels/783441128119730236/1207651082491142154)** anzugeben.

### Behoben

- Wenn Ende eines Events/Turniers gleich dem Start ist (kein Ende angegeben), dann wird dieses auch nicht mit dazu geschrieben.