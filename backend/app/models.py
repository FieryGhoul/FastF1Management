from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Season(Base):
    __tablename__ = "seasons"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    season: Mapped[int] = mapped_column(ForeignKey("seasons.year", ondelete="CASCADE"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(160))
    official_name: Mapped[str | None] = mapped_column(String(300))
    country: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(100))
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    format: Mapped[str | None] = mapped_column(String(50))
    f1_api_support: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("season", "round_number"),)


class RaceSession(Base):
    __tablename__ = "race_sessions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    abbreviation: Mapped[str] = mapped_column(String(8))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="scheduled")


class Driver(Base):
    __tablename__ = "drivers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    code: Mapped[str | None] = mapped_column(String(5), index=True)
    number: Mapped[str | None] = mapped_column(String(4))
    given_name: Mapped[str] = mapped_column(String(80))
    family_name: Mapped[str] = mapped_column(String(80))
    nationality: Mapped[str | None] = mapped_column(String(80))
    country_code: Mapped[str | None] = mapped_column(String(5))
    headshot_url: Mapped[str | None] = mapped_column(Text)


class Constructor(Base):
    __tablename__ = "constructors"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    nationality: Mapped[str | None] = mapped_column(String(80))
    color: Mapped[str | None] = mapped_column(String(12))


class SeasonEntry(Base):
    __tablename__ = "season_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(ForeignKey("seasons.year", ondelete="CASCADE"), index=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id", ondelete="CASCADE"))
    constructor_id: Mapped[str] = mapped_column(ForeignKey("constructors.id", ondelete="CASCADE"))
    round_from: Mapped[int | None] = mapped_column(Integer)
    round_to: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("season", "driver_id", "constructor_id", "round_from"),)


class RaceResult(Base):
    __tablename__ = "race_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("race_sessions.id", ondelete="CASCADE"), index=True)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id", ondelete="CASCADE"))
    constructor_id: Mapped[str | None] = mapped_column(ForeignKey("constructors.id", ondelete="SET NULL"))
    position: Mapped[int | None] = mapped_column(Integer)
    grid_position: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(120))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("session_id", "driver_id"),)


class Circuit(Base):
    __tablename__ = "circuits"
    slug: Mapped[str] = mapped_column(String(100), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(180))
    country: Mapped[str] = mapped_column(String(100))
    locality: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    length_km: Mapped[float | None] = mapped_column(Float)
    race_laps: Mapped[int | None] = mapped_column(Integer)
    lap_record: Mapped[str | None] = mapped_column(String(80))
    first_grand_prix: Mapped[int | None] = mapped_column(Integer)
    circuit_type: Mapped[str | None] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(Text)
    map_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CircuitAlias(Base):
    __tablename__ = "circuit_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(180), unique=True)
    circuit_slug: Mapped[str] = mapped_column(ForeignKey("circuits.slug", ondelete="CASCADE"))


class StandingSnapshot(Base):
    __tablename__ = "standing_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    round_number: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    data: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("season", "round_number", "kind"),)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(250), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DerivedArtifact(Base):
    __tablename__ = "derived_artifacts"
    key: Mapped[str] = mapped_column(String(250), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
