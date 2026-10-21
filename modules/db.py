"""
SQLAlchemy database models for tournament persistence.
"""
import sqlite3
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text, text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from pathlib import Path

DISCORD_ID_TYPE = String(32)

# Use SQLite database in data/
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "tournaments.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False},
    echo=False  # Set to True for SQL debugging
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Tournament(Base):
    """Main tournament record"""
    __tablename__ = "tournaments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    organizer_id = Column(DISCORD_ID_TYPE, index=True)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Discord identifiers
    guild_id = Column(DISCORD_ID_TYPE, index=True)
    channel_id = Column(DISCORD_ID_TYPE, index=True)
    message_id = Column(DISCORD_ID_TYPE, index=True, unique=True)
    
    # Tournament configuration
    max_participants = Column(Integer, nullable=True)
    max_rounds = Column(Integer, nullable=True)
    days_per_match = Column(Integer, default=7)
    tournament_style = Column(String, nullable=False, default="swiss", server_default="swiss")
    end_time = Column(DateTime, nullable=True)
    league_cooldown_percentage = Column(Integer, nullable=False, default=50, server_default="50")
    league_count_repeat_opponents = Column(Boolean, nullable=False, default=True, server_default="1")
    league_matches_before_cooldown = Column(Integer, nullable=False, default=1, server_default="1")
    
    # Status
    cancelled = Column(Boolean, default=False)

    # Format (e.g. Pauper, Modern, Legacy)
    format_name = Column(String, nullable=True)
    
    # Relationships
    participants = relationship("Participant", back_populates="tournament", cascade="all, delete-orphan")
    swiss_tournament = relationship("SwissTournamentRecord", back_populates="tournament", uselist=False, cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Tournament(id={self.id}, name='{self.name}', guild_id={self.guild_id})>"


class Participant(Base):
    """Tournament participants and their participation state"""
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), index=True)
    user_id = Column(DISCORD_ID_TYPE, index=True)
    state = Column(String)  # Stores ParticipationState values, including waitlist.
    joined_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('tournament_id', 'user_id', name='uq_participant_tournament_user'),
    )
    
    # Relationship
    tournament = relationship("Tournament", back_populates="participants")
    
    def __repr__(self):
        return f"<Participant(tournament_id={self.tournament_id}, user_id={self.user_id}, state='{self.state}')>"


class SwissTournamentRecord(Base):
    """Normalized Swiss tournament root record"""
    __tablename__ = "swiss_tournaments"

    tournament_id = Column(Integer, ForeignKey("tournaments.id"), primary_key=True)
    rounds_count = Column(Integer, nullable=False)
    winner_player_id = Column(DISCORD_ID_TYPE, nullable=True)

    tournament = relationship("Tournament", back_populates="swiss_tournament")
    players = relationship("SwissPlayer", back_populates="swiss_tournament", cascade="all, delete-orphan")
    rounds = relationship("SwissRound", back_populates="swiss_tournament", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SwissTournamentRecord(tournament_id={self.tournament_id}, rounds_count={self.rounds_count})>"


class SwissPlayer(Base):
    """Normalized Swiss player row"""
    __tablename__ = "swiss_players"

    id = Column(Integer, primary_key=True, index=True)
    swiss_tournament_id = Column(Integer, ForeignKey("swiss_tournaments.tournament_id"), index=True)
    player_id = Column(DISCORD_ID_TYPE, index=True)
    dropped = Column(Boolean, default=False)
    ready_to_play = Column(Boolean, nullable=False, default=False, server_default="0")
    ready_until = Column(DateTime, nullable=True)
    deck_link = Column(Text, nullable=True)
    deck_archetype = Column(String, nullable=True)

    swiss_tournament = relationship("SwissTournamentRecord", back_populates="players")

    def __repr__(self):
        return f"<SwissPlayer(swiss_tournament_id={self.swiss_tournament_id}, player_id={self.player_id})>"


class SwissRound(Base):
    """Normalized Swiss round row"""
    __tablename__ = "swiss_rounds"

    id = Column(Integer, primary_key=True, index=True)
    swiss_tournament_id = Column(Integer, ForeignKey("swiss_tournaments.tournament_id"), index=True)
    round_number = Column(Integer, nullable=False)
    message_id_pairings = Column(DISCORD_ID_TYPE, nullable=True)
    message_id_standings = Column(DISCORD_ID_TYPE, nullable=True)

    swiss_tournament = relationship("SwissTournamentRecord", back_populates="rounds")
    matches = relationship("SwissMatch", back_populates="swiss_round", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SwissRound(swiss_tournament_id={self.swiss_tournament_id}, round_number={self.round_number})>"


class SwissMatch(Base):
    """Normalized Swiss match row"""
    __tablename__ = "swiss_matches"

    id = Column(Integer, primary_key=True, index=True)
    swiss_round_id = Column(Integer, ForeignKey("swiss_rounds.id"), index=True)
    player1_id = Column(DISCORD_ID_TYPE, nullable=False)
    player2_id = Column(DISCORD_ID_TYPE, nullable=True)
    player1_wins = Column(Integer, nullable=False, default=0)
    player2_wins = Column(Integer, nullable=False, default=0)
    draws = Column(Integer, nullable=False, default=0)

    swiss_round = relationship("SwissRound", back_populates="matches")

    def __repr__(self):
        return f"<SwissMatch(swiss_round_id={self.swiss_round_id}, player1_id={self.player1_id}, player2_id={self.player2_id})>"


class DiscordUser(Base):
    """Discord user display name cache"""
    __tablename__ = "users"

    user_id = Column(DISCORD_ID_TYPE, primary_key=True)
    display_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<DiscordUser(user_id={self.user_id}, display_name='{self.display_name}')>"


class BannedUser(Base):
    """Users banned from tournament participation"""
    __tablename__ = "banned_users"

    user_id = Column(DISCORD_ID_TYPE, primary_key=True)
    banned_by = Column(DISCORD_ID_TYPE, nullable=True)
    reason = Column(Text, nullable=True)
    banned_at = Column(DateTime, default=datetime.utcnow)
    banned_until = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<BannedUser(user_id={self.user_id})>"


def _sqlite_column_types(table_name: str) -> dict[str, str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1]: row[2].upper() for row in rows}


def _migrate_discord_id_columns():
    expected_text_columns = {
        "tournaments": ["organizer_id", "guild_id", "channel_id", "message_id"],
        "participants": ["user_id"],
        "swiss_tournaments": ["winner_player_id"],
        "swiss_players": ["player_id"],
        "swiss_rounds": ["message_id_pairings", "message_id_standings"],
        "swiss_matches": ["player1_id", "player2_id"],
        "users": ["user_id"],
    }

    tables_to_migrate = []
    for table_name, columns in expected_text_columns.items():
        column_types = _sqlite_column_types(table_name)
        if any(column_types.get(column_name) == "INTEGER" for column_name in columns):
            tables_to_migrate.append(table_name)

    if not tables_to_migrate:
        return

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))

        if "tournaments" in tables_to_migrate:
            connection.execute(text("ALTER TABLE tournaments RENAME TO tournaments_old"))
            connection.execute(text("""
                CREATE TABLE tournaments (
                    id INTEGER NOT NULL,
                    name VARCHAR,
                    organizer_id VARCHAR(32),
                    description TEXT,
                    start_time DATETIME,
                    created_at DATETIME,
                    guild_id VARCHAR(32),
                    channel_id VARCHAR(32),
                    message_id VARCHAR(32),
                    max_participants INTEGER,
                    max_rounds INTEGER,
                    days_per_match INTEGER,
                    cancelled BOOLEAN,
                    PRIMARY KEY (id),
                    UNIQUE (message_id)
                )
            """))
            connection.execute(text("""
                INSERT INTO tournaments (
                    id, name, organizer_id, description, start_time, created_at,
                    guild_id, channel_id, message_id, max_participants, max_rounds,
                    days_per_match, cancelled
                )
                SELECT
                    id, name, CAST(organizer_id AS TEXT), description, start_time, created_at,
                    CAST(guild_id AS TEXT), CAST(channel_id AS TEXT), CAST(message_id AS TEXT),
                    max_participants, max_rounds, days_per_match, cancelled
                FROM tournaments_old
            """))
            connection.execute(text("DROP TABLE tournaments_old"))

        if "participants" in tables_to_migrate:
            connection.execute(text("ALTER TABLE participants RENAME TO participants_old"))
            connection.execute(text("""
                CREATE TABLE participants (
                    id INTEGER NOT NULL,
                    tournament_id INTEGER,
                    user_id VARCHAR(32),
                    state VARCHAR,
                    joined_at DATETIME,
                    PRIMARY KEY (id),
                    FOREIGN KEY(tournament_id) REFERENCES tournaments (id),
                    UNIQUE (tournament_id, user_id)
                )
            """))
            # Deduplicate participants during migration: keep first id for each (tournament_id, user_id)
            connection.execute(text("""
                INSERT INTO participants (id, tournament_id, user_id, state, joined_at)
                SELECT id, tournament_id, CAST(user_id AS TEXT), state, joined_at
                FROM participants_old
                WHERE id IN (
                    SELECT MIN(id) FROM participants_old
                    GROUP BY tournament_id, CAST(user_id AS TEXT)
                )
            """))
            connection.execute(text("DROP TABLE participants_old"))

        if "swiss_tournaments" in tables_to_migrate:
            connection.execute(text("ALTER TABLE swiss_tournaments RENAME TO swiss_tournaments_old"))
            connection.execute(text("""
                CREATE TABLE swiss_tournaments (
                    tournament_id INTEGER NOT NULL,
                    rounds_count INTEGER NOT NULL,
                    winner_player_id VARCHAR(32),
                    PRIMARY KEY (tournament_id),
                    FOREIGN KEY(tournament_id) REFERENCES tournaments (id)
                )
            """))
            connection.execute(text("""
                INSERT INTO swiss_tournaments (tournament_id, rounds_count, winner_player_id)
                SELECT tournament_id, rounds_count, CAST(winner_player_id AS TEXT)
                FROM swiss_tournaments_old
            """))
            connection.execute(text("DROP TABLE swiss_tournaments_old"))

        if "swiss_players" in tables_to_migrate:
            connection.execute(text("ALTER TABLE swiss_players RENAME TO swiss_players_old"))
            connection.execute(text("""
                CREATE TABLE swiss_players (
                    id INTEGER NOT NULL,
                    swiss_tournament_id INTEGER,
                    player_id VARCHAR(32),
                    dropped BOOLEAN,
                    deck_link TEXT,
                    deck_archetype VARCHAR,
                    PRIMARY KEY (id),
                    FOREIGN KEY(swiss_tournament_id) REFERENCES swiss_tournaments (tournament_id)
                )
            """))
            connection.execute(text("""
                INSERT INTO swiss_players (id, swiss_tournament_id, player_id, dropped, deck_link, deck_archetype)
                SELECT id, swiss_tournament_id, CAST(player_id AS TEXT), dropped, deck_link, deck_archetype
                FROM swiss_players_old
            """))
            connection.execute(text("DROP TABLE swiss_players_old"))

        if "swiss_rounds" in tables_to_migrate:
            connection.execute(text("ALTER TABLE swiss_rounds RENAME TO swiss_rounds_old"))
            connection.execute(text("""
                CREATE TABLE swiss_rounds (
                    id INTEGER NOT NULL,
                    swiss_tournament_id INTEGER,
                    round_number INTEGER NOT NULL,
                    message_id_pairings VARCHAR(32),
                    message_id_standings VARCHAR(32),
                    PRIMARY KEY (id),
                    FOREIGN KEY(swiss_tournament_id) REFERENCES swiss_tournaments (tournament_id)
                )
            """))
            connection.execute(text("""
                INSERT INTO swiss_rounds (
                    id, swiss_tournament_id, round_number, message_id_pairings, message_id_standings
                )
                SELECT
                    id, swiss_tournament_id, round_number,
                    CAST(message_id_pairings AS TEXT), CAST(message_id_standings AS TEXT)
                FROM swiss_rounds_old
            """))
            connection.execute(text("DROP TABLE swiss_rounds_old"))

        if "swiss_matches" in tables_to_migrate:
            connection.execute(text("ALTER TABLE swiss_matches RENAME TO swiss_matches_old"))
            connection.execute(text("""
                CREATE TABLE swiss_matches (
                    id INTEGER NOT NULL,
                    swiss_round_id INTEGER,
                    player1_id VARCHAR(32) NOT NULL,
                    player2_id VARCHAR(32),
                    player1_wins INTEGER NOT NULL,
                    player2_wins INTEGER NOT NULL,
                    draws INTEGER NOT NULL,
                    PRIMARY KEY (id),
                    FOREIGN KEY(swiss_round_id) REFERENCES swiss_rounds (id)
                )
            """))
            connection.execute(text("""
                INSERT INTO swiss_matches (
                    id, swiss_round_id, player1_id, player2_id, player1_wins, player2_wins, draws
                )
                SELECT
                    id, swiss_round_id, CAST(player1_id AS TEXT), CAST(player2_id AS TEXT),
                    player1_wins, player2_wins, draws
                FROM swiss_matches_old
            """))
            connection.execute(text("DROP TABLE swiss_matches_old"))

        if "users" in tables_to_migrate:
            connection.execute(text("ALTER TABLE users RENAME TO users_old"))
            connection.execute(text("""
                CREATE TABLE users (
                    user_id VARCHAR(32) NOT NULL,
                    display_name VARCHAR,
                    username VARCHAR,
                    updated_at DATETIME,
                    PRIMARY KEY (user_id)
                )
            """))
            connection.execute(text("""
                INSERT INTO users (user_id, display_name, username, updated_at)
                SELECT CAST(user_id AS TEXT), display_name, username, updated_at
                FROM users_old
            """))
            connection.execute(text("DROP TABLE users_old"))

        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tournaments_name ON tournaments (name)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tournaments_organizer_id ON tournaments (organizer_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tournaments_start_time ON tournaments (start_time)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tournaments_guild_id ON tournaments (guild_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tournaments_channel_id ON tournaments (channel_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tournaments_message_id ON tournaments (message_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_participants_id ON participants (id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_participants_tournament_id ON participants (tournament_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_participants_user_id ON participants (user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_swiss_players_id ON swiss_players (id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_swiss_players_swiss_tournament_id ON swiss_players (swiss_tournament_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_swiss_players_player_id ON swiss_players (player_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_swiss_rounds_id ON swiss_rounds (id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_swiss_rounds_swiss_tournament_id ON swiss_rounds (swiss_tournament_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_swiss_matches_id ON swiss_matches (id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_swiss_matches_swiss_round_id ON swiss_matches (swiss_round_id)"))
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_add_format_name_column():
    """Add format_name column to tournaments table if it doesn't exist yet."""
    col_types = _sqlite_column_types("tournaments")
    if "format_name" not in col_types:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tournaments ADD COLUMN format_name VARCHAR"))


def _migrate_add_league_columns():
    with engine.begin() as connection:
        col_types = {row[1] for row in connection.execute(text("PRAGMA table_info(tournaments)"))}
        if "tournament_style" not in col_types:
            connection.execute(text("ALTER TABLE tournaments ADD COLUMN tournament_style VARCHAR NOT NULL DEFAULT 'swiss'"))
        if "end_time" not in col_types:
            connection.execute(text("ALTER TABLE tournaments ADD COLUMN end_time DATETIME"))
        if "league_cooldown_percentage" not in col_types:
            connection.execute(text("ALTER TABLE tournaments ADD COLUMN league_cooldown_percentage INTEGER NOT NULL DEFAULT 50"))
        if "league_count_repeat_opponents" not in col_types:
            connection.execute(text("ALTER TABLE tournaments ADD COLUMN league_count_repeat_opponents BOOLEAN NOT NULL DEFAULT 1"))
        if "league_matches_before_cooldown" not in col_types:
            connection.execute(text("ALTER TABLE tournaments ADD COLUMN league_matches_before_cooldown INTEGER NOT NULL DEFAULT 1"))


def _migrate_add_player_readiness_column():
    with engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(swiss_players)"))}
        if "ready_to_play" not in columns:
            connection.execute(text("ALTER TABLE swiss_players ADD COLUMN ready_to_play BOOLEAN NOT NULL DEFAULT 0"))
        if "ready_until" not in columns:
            connection.execute(text("ALTER TABLE swiss_players ADD COLUMN ready_until DATETIME"))


def _ensure_banned_users_table():
    """Create banned_users table if it does not exist."""
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id VARCHAR(32) NOT NULL,
                banned_by VARCHAR(32),
                reason TEXT,
                banned_at DATETIME,
                banned_until DATETIME,
                PRIMARY KEY (user_id)
            )
        """))


def _migrate_add_banned_until_column():
    """Add banned_until column to banned_users table if it doesn't exist yet."""
    col_types = _sqlite_column_types("banned_users")
    if "banned_until" not in col_types:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE banned_users ADD COLUMN banned_until DATETIME"))


def init_db():
    """Create all tables in the database"""
    Base.metadata.create_all(bind=engine)
    _migrate_add_format_name_column()
    _migrate_add_league_columns()
    _migrate_add_player_readiness_column()
    _ensure_banned_users_table()
    _migrate_add_banned_until_column()
    print("✅ Database tables created")


def get_db():
    """Get a database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
