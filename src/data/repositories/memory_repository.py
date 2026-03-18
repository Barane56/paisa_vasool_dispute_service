# memory_repository.py — MemoryEpisodeRepository, MemorySummaryRepository, OpenQuestionRepository
from typing import Optional, List
from sqlalchemy import select, update, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from .base import BaseRepository
from src.data.models.postgres import DisputeMemoryEpisode, DisputeMemorySummary, DisputeOpenQuestion


class MemoryEpisodeRepository(BaseRepository[DisputeMemoryEpisode]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeMemoryEpisode, db)

    async def get_episodes_for_dispute(self, dispute_id: int, limit: int = 50) -> List[DisputeMemoryEpisode]:
        stmt = select(DisputeMemoryEpisode).where(DisputeMemoryEpisode.dispute_id == dispute_id).order_by(DisputeMemoryEpisode.created_at.asc()).limit(limit)
        return list((await self.db.execute(stmt)).scalars().all())

    async def count_for_dispute(self, dispute_id: int) -> int:
        return (await self.db.execute(select(func.count()).where(DisputeMemoryEpisode.dispute_id == dispute_id))).scalar_one()

    async def get_latest_n(self, dispute_id: int, n: int = 5) -> List[DisputeMemoryEpisode]:
        stmt = select(DisputeMemoryEpisode).where(DisputeMemoryEpisode.dispute_id == dispute_id).order_by(DisputeMemoryEpisode.created_at.desc()).limit(n)
        return list(reversed((await self.db.execute(stmt)).scalars().all()))

    async def upsert_embedding(self, episode_id: int, embedding: List[float]) -> None:
        """Persist a pgvector embedding for an episode after AI summary generation."""
        await self.db.execute(update(DisputeMemoryEpisode).where(DisputeMemoryEpisode.episode_id == episode_id).values(content_embedding=embedding))
        await self.db.flush()

    async def search_similar_by_customer(self, customer_id: str, query_embedding: List[float], top_k: int = 5, threshold: float = 0.75) -> List[dict]:
        """
        pgvector cosine similarity search scoped to a customer.
        Threshold guide: ≥0.90 near-duplicate, ≥0.80 same topic, ≥0.75 probable match, ≥0.65 loose.
        """
        vec_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"
        sql = text("""
            SELECT e.episode_id, e.dispute_id, e.episode_type, e.actor, e.content_text, e.created_at,
                   1 - (e.content_embedding <=> CAST(:vec AS vector)) AS similarity
            FROM dispute_memory_episode e
            JOIN dispute_master d ON d.dispute_id = e.dispute_id
            WHERE d.customer_id = :customer_id
              AND e.content_embedding IS NOT NULL
              AND 1 - (e.content_embedding <=> CAST(:vec AS vector)) >= :threshold
            ORDER BY similarity DESC
            LIMIT :top_k
        """)
        rows = (await self.db.execute(sql, {"vec": vec_literal, "customer_id": customer_id, "threshold": threshold, "top_k": top_k})).mappings().all()
        return [{"episode_id": r["episode_id"], "dispute_id": r["dispute_id"], "episode_type": r["episode_type"],
                 "actor": r["actor"], "content_text": r["content_text"][:400],
                 "similarity": round(float(r["similarity"]), 4), "created_at": r["created_at"]} for r in rows]


class MemorySummaryRepository(BaseRepository[DisputeMemorySummary]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeMemorySummary, db)

    async def get_for_dispute(self, dispute_id: int) -> Optional[DisputeMemorySummary]:
        result = await self.db.execute(select(DisputeMemorySummary).where(DisputeMemorySummary.dispute_id == dispute_id))
        return result.scalar_one_or_none()


class OpenQuestionRepository(BaseRepository[DisputeOpenQuestion]):
    def __init__(self, db: AsyncSession):
        super().__init__(DisputeOpenQuestion, db)

    async def get_by_id(self, question_id: int, **kwargs) -> Optional[DisputeOpenQuestion]:
        result = await self.db.execute(select(DisputeOpenQuestion).where(DisputeOpenQuestion.question_id == question_id))
        return result.scalar_one_or_none()

    async def get_pending_for_dispute(self, dispute_id: int) -> List[DisputeOpenQuestion]:
        stmt = select(DisputeOpenQuestion).where(and_(DisputeOpenQuestion.dispute_id == dispute_id, DisputeOpenQuestion.status == "PENDING")).order_by(DisputeOpenQuestion.created_at.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_all_for_dispute(self, dispute_id: int) -> List[DisputeOpenQuestion]:
        stmt = select(DisputeOpenQuestion).where(DisputeOpenQuestion.dispute_id == dispute_id).order_by(DisputeOpenQuestion.created_at.asc())
        return list((await self.db.execute(stmt)).scalars().all())

    async def expire_all_for_dispute(self, dispute_id: int) -> None:
        await self.db.execute(
            update(DisputeOpenQuestion)
            .where(and_(DisputeOpenQuestion.dispute_id == dispute_id, DisputeOpenQuestion.status == "PENDING"))
            .values(status="EXPIRED")
        )
