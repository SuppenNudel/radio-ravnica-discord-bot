# Spelltable Common Views

This module provides reusable Discord UI views, modals, and helper functions for managing the user interface and interactions during Spelltable tournaments. It is used by the Spelltable tournament cog and model to handle match reporting, round management, player actions, and tournament lifecycle events.

## Features

- **Finish Tournament**: UI for organizers to finish a tournament and announce the winner.
- **Match Reporting**: Modals and views for players to report match results, including scores and optional messages.
- **Player Management**: UI for dropping, kicking, or confirming player actions during a tournament.
- **Round Management**: Views for starting the next round, updating pairings, and posting standings.
- **Tournament Cancellation**: Modal for organizers to cancel a tournament and notify participants.
- **Helper Functions**: Functions to update pairings, standings, and handle round transitions.

## Key Classes and Functions

- [`FinishTournamentView`](../modules/spelltable/common_views.py#L10): View for finishing a tournament.
- [`ReportMatchModal`](../modules/spelltable/common_views.py#L58): Modal for reporting match results.
- [`ConfirmDropModal`](../modules/spelltable/common_views.py#L154): Modal for confirming a player drop.
- [`ConfirmKickView`](../modules/spelltable/common_views.py#L221): View for confirming a player kick.
- [`CancelTournamentModal`](../modules/spelltable/common_views.py#L252): Modal for cancelling a tournament.
- [`KickPlayerModal`](../modules/spelltable/common_views.py#L273): Modal for kicking a player.
- [`ReportMatchView`](../modules/spelltable/common_views.py#L324): View for reporting matches and player actions.
- [`StartNextRoundView`](../modules/spelltable/common_views.py#L483): View for starting the next round.
- [`update_pairings`](../modules/spelltable/common_views.py#L128), [`update_standings`](../modules/spelltable/common_views.py#L186), [`post_pairings`](../modules/spelltable/common_views.py#L401), [`next_round`](../modules/spelltable/common_views.py#L426): Helper functions for managing tournament state and Discord messages.

## Usage

This module is intended to be imported and used by the Spelltable tournament cog and model. It provides the UI and logic for all in-tournament interactions and state changes.

---

See also:
- [spelltable_tournament.md](../../documentation/spelltable_tournament.md) for the Discord cog documentation
- [tournament_model.md](../../documentation/tournament_model.md) for the tournament model documentation

For more details, see the source code and docstrings in [`common_views.py`](../modules/spelltable/common_views.py).
