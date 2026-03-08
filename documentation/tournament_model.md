# Spelltable Tournament Model Cog

This module provides the core logic and data structures for managing Spelltable tournaments in a Discord bot environment. It handles tournament creation, participant management, Swiss pairings, and message updates.

## Features

- **Tournament Management**: Create, save, load, and update tournaments.
- **Participant Handling**: Track participation status (Participate, Tentative, Decline), waitlists, and organizers.
- **Swiss Tournament Integration**: Supports Swiss pairing logic and standings.
- **Discord Integration**: Updates Discord messages and embeds with tournament information.
- **Persistence**: Serializes and deserializes tournaments to JSON files for persistence.
- **Calendar Generation**: Generates a visual calendar of upcoming and ongoing tournaments.

## Key Classes and Functions

### `ParticipationState`
An enum representing a user's participation status:
- `PARTICIPATE`
- `TENTATIVE`
- `DECLINE`

### `SpelltableTournament`
Main class representing a tournament. Key methods:
- `serialize` / `deserialize`: For saving/loading tournament state.
- `user_state`: Update a user's participation status.
- `to_embed`: Generate a Discord embed with tournament info.
- `get_pairings`, `standings_to_image`, `pairings_to_image`: Generate pairings and standings, including image exports.
- `save_tournament`: Persist tournament data to disk.

### Utility Functions

- `save_tournaments()`: Save all active tournaments.
- `load_tournaments(bot)`: Load tournaments from disk.
- `generate_tournament_message(tournaments)`: Create a summary message for all tournaments.
- `update_tournament_message(bot, trigger_guild)`: Update the main tournament overview message in Discord.

## File Structure

- Tournament data is stored in the `tournaments/` folder as JSON files.
- Concluded tournaments are moved to `tournaments/concluded/`.
- Temporary images (standings, pairings) are saved in the `tmp/` folder.

## Usage

This cog is intended to be used as part of a Discord bot for managing Magic: The Gathering tournaments. It should be imported and used by higher-level command cogs that handle user interaction.

---

For more details, see the [tournament_model.py](../modules/spelltable/tournament_model.py) source code and its docstrings.

---

See also:
- [spelltable_tournament.md](spelltable_tournament.md) for documentation on the Discord cog that uses this model.
- [common_views.md](common_views.md) for documentation on the shared UI and helper functions.
