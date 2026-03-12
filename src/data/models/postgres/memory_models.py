# memory_models.py — DisputeMemoryEpisode, DisputeMemorySummary
from sqlalchemy import (
    Column, Integer, String, Text,
    TIMESTAMP, ForeignKey, Index, func, text,
)
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.orm import relationship
from .base import Base


class DisputeMemoryEpisode(Base):
    __tablename__ = "dispute_memory_episode"

    episode_id        = Column(Integer, primary_key=True)
    dispute_id        = Column(Integer, ForeignKey("dispute_master.dispute_id", ondelete="CASCADE"), nullable=False)
    episode_type      = Column(String(50), nullable=False)
    actor             = Column(String(50), nullable=False)
    content_text      = Column(Text, nullable=False)
    content_embedding = Column(VECTOR(768), nullable=True)
    email_id          = Column(Integer, ForeignKey("email_inbox.email_id", ondelete="SET NULL"), nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    dispute      = relationship("DisputeMaster", back_populates="episodes",   lazy="joined")
    source_email = relationship("EmailInbox",    back_populates="episodes",   lazy="select")
    open_questions_asked = relationship(
        "DisputeOpenQuestion", back_populates="asked_episode",
        foreign_keys="DisputeOpenQuestion.asked_in_episode_id", lazy="select",
    )
    open_questions_answered = relationship(
        "DisputeOpenQuestion", back_populates="answered_episode",
        foreign_keys="DisputeOpenQuestion.answered_in_episode_id", lazy="select",
    )
    memory_summaries = relationship("DisputeMemorySummary", back_populates="covered_up_to_episode", lazy="select")

    __table_args__ = (
        Index("ix_episode_dispute_id",   "dispute_id"),
        Index("ix_episode_episode_type", "episode_type"),
        Index("ix_episode_created_at",   "created_at"),
    )


class DisputeMemorySummary(Base):
    __tablename__ = "dispute_memory_summary"

    summary_id               = Column(Integer, primary_key=True)
    dispute_id               = Column(Integer, ForeignKey("dispute_master.dispute_id",         ondelete="CASCADE"),  unique=True, nullable=False)
    summary_text             = Column(Text, nullable=False)
    covered_up_to_episode_id = Column(Integer, ForeignKey("dispute_memory_episode.episode_id", ondelete="SET NULL"), nullable=True)
    version                  = Column(Integer, default=1, server_default=text("1"), nullable=False)
    updated_at               = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    dispute               = relationship("DisputeMaster",        back_populates="memory_summary",   lazy="joined")
    covered_up_to_episode = relationship("DisputeMemoryEpisode", back_populates="memory_summaries", lazy="select")

    __table_args__ = (Index("ix_memory_summary_dispute_id", "dispute_id"),)
