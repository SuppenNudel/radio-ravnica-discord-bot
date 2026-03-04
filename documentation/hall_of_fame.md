# Hall of Fame - Tournament Statistics & Leaderboards

## Overview

The Hall of Fame system tracks tournament winners and participant match wins across all completed tournaments. It provides persistent statistics for individual players and serves as a leaderboard to showcase the most successful tournament participants.

## Features

### 1. **Automatic Tournament Result Tracking**
- When a tournament concludes, all participant results are automatically recorded
- Tracks:
  - Tournament placement (1st, 2nd, 3rd, etc.)
  - Match record (Wins-Losses-Draws)
  - Individual match results
  - Tournament title and completion date

### 2. **Player Statistics**
Each player has a profile that includes:
- **Match Statistics**
  - Total wins, losses, and draws
  - Win/Loss/Draw record (W-L-D format)
  - Match win percentage
  
- **Tournament Statistics**
  - Number of tournament participations
  - Tournament victories (1st place finishes)
  - Top 3 finishes
  - Top 8 finishes

### 3. **Persistent Data Storage**
- All data is stored in `hall_of_fame.json` in the root directory
- Data persists across bot restarts
- Automatic saving after each tournament conclusion

## Discord Commands

### `/hall_of_fame [sort_by]`
Display the hall of fame leaderboard with various sorting options.

**Parameters:**
- `sort_by` (optional): Sorting criteria for the ranking
  - **Tournament Winners** (default): Shows players with most tournament victories
  - **Match Wins**: Shows players with most individual match wins
  - **Match Winrate**: Shows players with highest match win percentage
  - **Tournament Participations**: Shows most active players
  - **Top 3 Finishes**: Shows players with most podium finishes
  - **Top 8 Finishes**: Shows players with most top-8 finishes

**Example:**
```
/hall_of_fame sort_by:Match Wins
```

**Output:** Displays top 20 players in the selected category with their statistics

### `/my_stats`
Display your personal tournament statistics.

**Output:**
- Your match record and win percentage
- Tournament participation count
- Number of victories, top 3s, and top 8s
- Your last 5 tournament placements with match records

**Example:**
```
/my_stats
```

### `/top_players [category]`
Show the top 10 players in a specific category.

**Parameters:**
- `category` (optional): Category to display
  - **Turniersiege** (default): Tournament victories
  - **Matchsiege**: Match wins
  - **Winrate**: Match win percentage
  - **Turnierteilnahmen**: Tournament participations
  - **Top 3 Finishes**: Top 3 finishes

**Example:**
```
/top_players category:Winrate
```

## Data Structure

### ParticipantStats
```json
{
  "user_id": 123456789,
  "username": "PlayerName",
  "total_match_wins": 42,
  "total_match_losses": 18,
  "total_draws": 3,
  "tournament_participations": 5,
  "tournament_winners": 2,
  "tournament_top3s": 3,
  "tournament_top8s": 0,
  "last_updated": "2026-03-03T15:30:00.000000",
  "match_history": ["win", "loss", "win", ...]
}
```

### TournamentRecord
```json
{
  "tournament_id": "guild_id/channel_id/message_id",
  "tournament_title": "Standard Tournament #1",
  "guild_id": 123456789,
  "completed_date": "2026-03-03T15:30:00.000000",
  "results": [
    {
      "user_id": 123456789,
      "username": "PlayerName",
      "placement": 1,
      "match_record": "5-1-0"
    },
    ...
  ]
}
```

## Integration with Tournaments

### Automatic Update on Tournament Completion

When a tournament organizer clicks **"🏆 Turnier abschließen"** (Finish Tournament):

1. The winner is determined using Swiss-system tiebreakers
2. Players are sorted by standings
3. The tournament results are automatically added to the Hall of Fame
4. Each participant's statistics are updated with:
   - Their placement in the tournament
   - Their final match record
   - Tournament participation count
   - Placement-specific achievements (victories, top 3s, etc.)

### Data Collected

For each tournament result:
- Tournament ID (auto-generated from message location)
- Tournament title (as set by organizer)
- Guild ID (for filtering guild-specific tournaments)
- Completion date
- Final standings for all non-dropped players
- Each player's match record from the tournament

## Usage Examples

### Viewing the Hall of Fame
```
User: /hall_of_fame
Bot: [Displays top 20 players with most tournament victories]
```

### Checking Personal Stats
```
User: /my_stats
Bot: [Displays your match record, tournament wins, placements, etc.]
```

### Finding the Best Match Player
```
User: /top_players category:Matchsiege
Bot: [Shows top 10 players by individual match wins]
```

### Most Consistent Players
```
User: /hall_of_fame sort_by:Match Winrate
Bot: [Shows top 20 players sorted by match win percentage]
```

## Statistics Calculations

### Match Win Percentage
```
Match Win % = (Wins + (Draws × 0.5)) / Total Matches
```

### Tournament Tiebreakers (Swiss System)
1. Match Points (3 points per win, 1 point per draw, 0 per loss)
2. Opponents' Match Win Percentage (OMWP)
3. Game Win Percentage (GWP)
4. Opponents' Game Win Percentage (OGWP)

## Files

- **Storage:** `hall_of_fame.json` - Persistent data file
- **Module:** `modules/hall_of_fame.py` - Core logic and data structures
- **Cog:** `cogs/hall_of_fame.py` - Discord commands
- **Integration:** `modules/spelltable/common_views.py` - Tournament conclusion integration

## Implementation Details

### Classes

#### `ParticipantStats`
- Represents a single player's cumulative statistics
- Methods: `add_match()`, `add_tournament()`, `get_match_record()`, `get_match_win_percentage()`

#### `TournamentRecord`
- Represents a single completed tournament
- Stores all final results and standings

#### `HallOfFame`
- Main manager class
- Methods:
  - `add_participant()` - Create or get participant stats
  - `add_tournament_result()` - Record a completed tournament
  - `get_top_participants()` - Get ranked leaderboards
  - `get_user_stats()` - Get player details
  - `get_tournament_results()` - Get specific tournament details
  - `get_guild_tournaments()` - Get tournaments for a guild

### Functions

#### `get_hall_of_fame()`
Returns the global Hall of Fame instance (singleton pattern)

#### `update_hall_of_fame(tournament)`
Called automatically when a tournament concludes - updates the global Hall of Fame with tournament results

## Future Enhancement Ideas

- Monthly leaderboards
- Seasonal statistics resets
- Award badges for milestones (e.g., "10 Wins", "Perfect Record")
- Tournament history search
- Matchup statistics between players
- Win/loss streaks tracking
- Format-specific statistics (if multi-format tournaments are supported)

## Notes

- Hall of Fame is global and shared across all guilds
- Tournament IDs are based on the tournament message location (guild/channel/message)
- Only non-dropped players are included in final standings
- Statistics are cumulative and never reset (unless manually edited)
