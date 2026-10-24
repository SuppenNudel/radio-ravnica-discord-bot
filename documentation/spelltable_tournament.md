# Spelltable Tournament Cog

This cog provides the Discord bot commands and user interface for managing Spelltable tournaments on the server. It connects the tournament model logic with Discord interactions, allowing users to create, join, edit, and manage tournaments directly from Discord.

## Features

- **Slash Command Integration**: Create tournaments using `/turnier_erstellen`, choosing `Swiss` or `Liga` in the required `turnierart` option.
- **Interactive Views**: Join, leave, edit, or start tournaments via Discord UI buttons.
- **Role-based Permissions**: Only authorized users can create or manage tournaments.
- **Tournament Editing**: Organizers can edit tournament details (description, time, max players, rounds, etc.) through modals.
- **Swiss Tournament Start**: Automatically initializes Swiss tournaments and handles round progression.
- **Leagues**: Players choose their opponents and report matches independently, without assigned pairings, round deadlines, or a round limit. A fixed end date/time is mandatory.
- **Participation Management**: Users can set their participation status (Participate, Tentative, Decline) and organizers can manage participants.
- **Automated Updates**: Periodically updates the tournament overview message.
- **Historical Pairings Reconciliation**: On startup/reconnect, compares historical pairings images with the posted attachments and skips messages that already match and have no buttons. Stale buttons are removed without re-uploading an unchanged image. If Discord reports the old-message edit limit (error `30046`), the bot logs a warning and stops processing the remaining historical rounds for that tournament. Existing messages are kept; pending corrections are checked again on a later startup/reconnect. Active-round updates are unaffected.


## League Workflow

1. Use `/turnier_erstellen` with `turnierart: Liga`.
2. Set a start date/time. The end date/time is optional; if set, it must be after the start and still in the future when submitting or starting the league. Without an end date the league remains open until cancelled. Dates use the existing Europe/Berlin date parser.
3. Players register with the existing participation buttons. The organizer starts the league with at least two participants. Registration remains open via **Teilnehmen** on the league message until the league ends or is cancelled. Late participants immediately appear in standings and opponent lists. Existing participant limits, waitlists, and tournament bans still apply; promoted waitlisted users join the active roster automatically. Returning participants retain their match history.
4. From the start until the optional end date (or cancellation), participants use **Ergebnis melden** to select an opponent and enter game wins and draws. Both players must be active participants; self-matches and empty results are rejected. Report each match once, by either participant. Before starting, the creator can use **Rematch-Regeln** to set a cooldown percentage from 0 to 100 and choose whether every match or only different opponents count. The defaults remain 50% and every match counts. The percentage applies to the other active players, excluding the two players in the pair, rounded up. For example, with six active players and the default rules, each needs two matches against other opponents. Repeated matches against the same other opponent count separately in the default mode, provided those matches satisfy their own pair cooldowns. Progress is counted separately for both players since their last meeting. Dropped players no longer count; if only two active players remain, rematches have no cooldown. The opponent dropdown shows ✅ for playable opponents and ⏳ with remaining counts in the selected counting mode. Rules are displayed in the tournament details, persisted across restarts, and fixed once the league starts; 0% disables the cooldown. Counts are rechecked on submission, and cooldown progress is derived from match history.
5. The league message shows current standings. Its message text (not the embed) lists clickable participant mentions followed by display names in brackets. Leading icons indicate availability: 🟢 ready, 🔴 not ready, ⛔ dropped, and ⚪ league ended. **Bereit** opens a form asking for a positive number of minutes (default 60). The player stays ready until that deadline, capped at the league end; the end time is shown beside their name. Readiness automatically expires, with the visible list refreshed by the background check within 30 seconds. Pressing **Bereit** again replaces the deadline, while **Nicht bereit** clears readiness immediately. Durations survive bot restarts; everyone initially starts as not ready. Status updates do not ping the roster. Large rosters use arrow buttons to browse all participants within Discord's message text limit. League messages do not offer deck-list submissions or include a Deck column in standings images. Scoring uses the selected Matchpunkte or Elo system. Different players may play different numbers of matches.
6. If an end time is configured, reports are rejected at that time, including from already-open result forms. A background check runs every 30 seconds to finalize standings. Restarts finalize leagues whose deadlines passed while the bot was offline. Leagues with no results close without a winner. Without an end time, the league remains open until cancelled.

The creator can set **Matches vor Cooldown** in **Rematch-Regeln** before starting. The default is 1; with 2, each pair may play two matches before both players must clear their cooldown. After clearing it, the next block of two matches is available. This counts reported matches, not individual games within a match. New participants change the active-player count used by percentage-based cooldowns.

The creator can choose **Matchpunkte** or **Elo-System**. Match points remain the default. Elo starts each player at 1000 with K=32. For a match, expected score is `1 / (1 + 10^((opponent rating - player rating) / 400))`; the result score is 1 for a win, 0.5 for a draw, and 0 for a loss. Both ratings update simultaneously by `32 * (result score - expected score)`. The reset interval is configured in days; `0` disables it. At each interval, every rating moves halfway back toward 1000. Mode, ratings, and reset time persist across restarts. Elo standings use rating order, and the league embed description explains the formula. Open-ended Elo leagues continue to receive scheduled soft resets.

The creator can choose **Matchpunkte** (default) or **Elo-System**, plus the soft-reset interval in days; `0` disables resets. Elo starts at 1000 with K=32. For each match, expected score is `1 / (1 + 10^((opponent rating - player rating) / 400))`; result is 1 for a win, 0.5 for a draw, and 0 for a loss. Both ratings update simultaneously by `32 * (result - expected score)`. At each configured interval, ratings move halfway back toward 1000. Elo mode, ratings, and the reset cursor persist across restarts, and Elo standings are sorted by rating. The league embed description includes the formula. An Elo league without an end date remains open while its soft-reset schedule continues.

League settings and results are stored in SQLite. Startup adds the new style, end-time, Elo, and rule columns automatically; existing tournaments remain Swiss. Tournament style is chosen at creation and cannot be changed after creation.

## Key Classes and Functions

### [`ParticipationView`](../cogs/spelltable/spelltable_tournament.py#L25)
A Discord UI view for participants to join, leave, or edit their tournament status.

### [`EditTournamentView`](../cogs/spelltable/spelltable_tournament.py#L150)
A Discord UI view for organizers to edit tournament details before starting.

### [`SpelltableTournamentManager`](../cogs/spelltable/spelltable_tournament.py#L282)
Main cog class that registers commands and manages tournament lifecycle events.
- [`on_ready`](../cogs/spelltable/spelltable_tournament.py#L288): Loads tournaments on bot startup.
- `/turnier_erstellen`: Slash command to create a new Swiss tournament or league.
- [`schedule_midnight_update`](../cogs/spelltable/spelltable_tournament.py#L391): Periodic task to update the tournament overview message.

## Usage

This cog should be loaded by the main bot and is intended for use by server members to manage Magic: The Gathering tournaments. It relies on the core logic in the [`tournament_model.py`](../modules/spelltable/tournament_model.py) module.

---

For more details, see the [tournament_model.md](../documentation/tournament_model.md) documentation, the [common_views.md](common_views.md) documentation, and the source code in `spelltable_tournament.py`.
