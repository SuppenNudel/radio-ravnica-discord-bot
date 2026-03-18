"""
SQLAlchemy database models for tournament persistence.
"""
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from pathlib import Path

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
    organizer_id = Column(Integer, index=True)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Discord identifiers
    guild_id = Column(Integer, index=True)
    channel_id = Column(Integer, index=True)
    message_id = Column(Integer, index=True, unique=True)
    
    # Tournament configuration
    max_participants = Column(Integer, nullable=True)
    max_rounds = Column(Integer, nullable=True)
    days_per_match = Column(Integer, default=7)
    
    # Status
    cancelled = Column(Boolean, default=False)
    
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
    user_id = Column(Integer, index=True)
    state = Column(String)  # 'participate', 'tentative', 'decline', 'waitlist'
    joined_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    tournament = relationship("Tournament", back_populates="participants")
    
    def __repr__(self):
        return f"<Participant(tournament_id={self.tournament_id}, user_id={self.user_id}, state='{self.state}')>"


class SwissTournamentRecord(Base):
    """Normalized Swiss tournament root record"""
    __tablename__ = "swiss_tournaments"

    id = Column(Integer, primary_key=True, index=True)
    tournament_id = Column(Integer, ForeignKey("tournaments.id"), unique=True, index=True)
    rounds_count = Column(Integer, nullable=False)
    winner_player_id = Column(Integer, nullable=True)

    tournament = relationship("Tournament", back_populates="swiss_tournament")
    players = relationship("SwissPlayer", back_populates="swiss_tournament", cascade="all, delete-orphan")
    rounds = relationship("SwissRound", back_populates="swiss_tournament", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SwissTournamentRecord(tournament_id={self.tournament_id}, rounds_count={self.rounds_count})>"


class SwissPlayer(Base):
    """Normalized Swiss player row"""
    __tablename__ = "swiss_players"

    id = Column(Integer, primary_key=True, index=True)
    swiss_tournament_id = Column(Integer, ForeignKey("swiss_tournaments.id"), index=True)
    player_id = Column(Integer, index=True)
    dropped = Column(Boolean, default=False)
    deck_link = Column(Text, nullable=True)
    deck_archetype = Column(String, nullable=True)

    swiss_tournament = relationship("SwissTournamentRecord", back_populates="players")

    def __repr__(self):
        return f"<SwissPlayer(swiss_tournament_id={self.swiss_tournament_id}, player_id={self.player_id})>"


class SwissRound(Base):
    """Normalized Swiss round row"""
    __tablename__ = "swiss_rounds"

    id = Column(Integer, primary_key=True, index=True)
    swiss_tournament_id = Column(Integer, ForeignKey("swiss_tournaments.id"), index=True)
    round_number = Column(Integer, nullable=False)
    message_id_pairings = Column(Integer, nullable=True)
    message_id_standings = Column(Integer, nullable=True)

    swiss_tournament = relationship("SwissTournamentRecord", back_populates="rounds")
    matches = relationship("SwissMatch", back_populates="swiss_round", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SwissRound(swiss_tournament_id={self.swiss_tournament_id}, round_number={self.round_number})>"


class SwissMatch(Base):
    """Normalized Swiss match row"""
    __tablename__ = "swiss_matches"

    id = Column(Integer, primary_key=True, index=True)
    swiss_round_id = Column(Integer, ForeignKey("swiss_rounds.id"), index=True)
    player1_id = Column(Integer, nullable=False)
    player2_id = Column(Integer, nullable=True)
    player1_wins = Column(Integer, nullable=False, default=0)
    player2_wins = Column(Integer, nullable=False, default=0)
    draws = Column(Integer, nullable=False, default=0)

    swiss_round = relationship("SwissRound", back_populates="matches")

    def __repr__(self):
        return f"<SwissMatch(swiss_round_id={self.swiss_round_id}, player1_id={self.player1_id}, player2_id={self.player2_id})>"


def init_db():
    """Create all tables in the database"""
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created")


def get_db():
    """Get a database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
