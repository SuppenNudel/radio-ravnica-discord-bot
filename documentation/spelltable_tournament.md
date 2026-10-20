# Spelltable Tournament Cog

This cog provides the Discord bot commands and user interface for managing Spelltable tournaments on the server. It connects the tournament model logic with Discord interactions, allowing users to create, join, edit, and manage tournaments directly from Discord.

## Features

- **Slash Command Integration**: Create tournaments using `/erstelle_turnier`.
- **Interactive Views**: Join, leave, edit, or start tournaments via Discord UI buttons.
- **Role-based Permissions**: Only authorized users can create or manage tournaments.
- **Tournament Editing**: Organizers can edit tournament details (description, time, max players, rounds, etc.) through modals.
- **Swiss Tournament Start**: Automatically initializes Swiss tournaments and handles round progression.
- **Participation Management**: Users can set their participation status (Participate, Tentative, Decline) and organizers can manage participants.
- **Automated Updates**: Periodically updates the tournament overview message.
- **Historical Pairings Reconciliation**: On startup/reconnect, compares historical pairings images with the posted attachments and skips messages that already match and have no buttons. Stale buttons are removed without re-uploading an unchanged image. If Discord reports the old-message edit limit (error `30046`), the bot logs a warning and stops processing the remaining historical rounds for that tournament. Existing messages are kept; pending corrections are checked again on a later startup/reconnect. Active-round updates are unaffected.


## Key Classes and Functions

### [`ParticipationView`](../cogs/spelltable/spelltable_tournament.py#L25)
A Discord UI view for participants to join, leave, or edit their tournament status.

### [`EditTournamentView`](../cogs/spelltable/spelltable_tournament.py#L150)
A Discord UI view for organizers to edit tournament details before starting.

### [`SpelltableTournamentManager`](../cogs/spelltable/spelltable_tournament.py#L282)
Main cog class that registers commands and manages tournament lifecycle events.
- [`on_ready`](../cogs/spelltable/spelltable_tournament.py#L288): Loads tournaments on bot startup.
- [`/erstelle_turnier`](../cogs/spelltable/spelltable_tournament.py#L344): Slash command to create a new tournament.
- [`schedule_midnight_update`](../cogs/spelltable/spelltable_tournament.py#L391): Periodic task to update the tournament overview message.

## Usage

This cog should be loaded by the main bot and is intended for use by server members to manage Magic: The Gathering tournaments. It relies on the core logic in the [`tournament_model.py`](../modules/spelltable/tournament_model.py) module.

---

For more details, see the [tournament_model.md](../documentation/tournament_model.md) documentation, the [common_views.md](common_views.md) documentation, and the source code in `spelltable_tournament.py`.
