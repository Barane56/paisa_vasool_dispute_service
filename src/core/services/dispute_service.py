import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List

from src.data.repositories.repositories import (
    DisputeRepository, DisputeTypeRepository, DisputeAIAnalysisRepository,
    DisputeAssignmentRepository, MemoryEpisodeRepository,
    MemorySummaryRepository, OpenQuestionRepository, UserRepository,
)
from src.data.models.postgres.models import (
    DisputeAssignment, DisputeActivityLog, DisputeStatusHistory, DisputeOpenQuestion,
)
from src.core.exceptions import (
    DisputeNotFoundError, UserNotFoundError, AnalysisNotFoundError,
    SummaryNotFoundError, QuestionNotFoundError, DisputeTypeNotFoundError,
)
from src.schemas.schemas import (
    DisputeStatusUpdate, DisputeAssignRequest, DisputeTimelineResponse,
    TimelineEpisodeResponse, TimelineAttachment, QuestionStatusUpdate,
)

logger = logging.getLogger(__name__)


class DisputeService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.dispute_repo = DisputeRepository(db)
        self.dtype_repo = DisputeTypeRepository(db)
        self.analysis_repo = DisputeAIAnalysisRepository(db)
        self.assign_repo = DisputeAssignmentRepository(db)
        self.ep_repo = MemoryEpisodeRepository(db)
        self.sum_repo = MemorySummaryRepository(db)
        self.q_repo = OpenQuestionRepository(db)
        self.user_repo = UserRepository(db)

    async def get_dispute(self, dispute_id: int):
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise DisputeNotFoundError(dispute_id)
        return dispute

    async def list_disputes(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        customer_id: Optional[str] = None,
        assigned_to: Optional[int] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ):
        return await self.dispute_repo.get_filtered(
            status=status,
            priority=priority,
            customer_id=customer_id,
            assigned_to=assigned_to,
            search=search,
            limit=limit,
            offset=offset,
        )

    async def update_status(self, dispute_id: int, data: DisputeStatusUpdate, performed_by: int):
        dispute = await self.get_dispute(dispute_id)
        old_status = dispute.status

        await self.dispute_repo.update_status(dispute_id, data.status)

        log = DisputeActivityLog(
            dispute_id=dispute_id,
            action_type="STATUS_CHANGED",
            performed_by=performed_by,
            notes=f"Status changed from {old_status} to {data.status}. {data.notes or ''}",
        )
        self.db.add(log)

        history = DisputeStatusHistory(
            dispute_id=dispute_id,
            action_type="STATUS_CHANGED",
            performed_by=performed_by,
            notes=f"{old_status} → {data.status}. {data.notes or ''}",
        )
        self.db.add(history)

        # Expire pending questions on closure
        if data.status in ("RESOLVED", "CLOSED"):
            await self.q_repo.expire_all_for_dispute(dispute_id)

        await self.db.commit()

    async def assign_dispute(self, dispute_id: int, data: DisputeAssignRequest, performed_by: int):
        await self.get_dispute(dispute_id)

        await self.assign_repo.deactivate_existing(dispute_id)

        user = await self.user_repo.get_by_id(data.user_id)
        if not user:
            raise UserNotFoundError(data.user_id)

        assignment = DisputeAssignment(
            dispute_id=dispute_id,
            assigned_to=data.user_id,
            status="ACTIVE",
        )
        self.db.add(assignment)

        log = DisputeActivityLog(
            dispute_id=dispute_id,
            action_type="ASSIGNED",
            performed_by=performed_by,
            notes=f"Assigned to {user.name}. {data.notes or ''}",
        )
        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(assignment)
        return assignment, user

    async def get_my_disputes(self, user_id: int, limit: int, offset: int):
        return await self.dispute_repo.get_filtered(
            assigned_to=user_id,
            status=None,
            limit=limit,
            offset=offset,
        )

    async def get_timeline(self, dispute_id: int) -> DisputeTimelineResponse:
        dispute = await self.get_dispute(dispute_id)
        episodes = await self.ep_repo.get_episodes_for_dispute(dispute_id)
        pending_qs = await self.q_repo.get_pending_for_dispute(dispute_id)
        active_assignment = await self.assign_repo.get_active_assignment(dispute_id)

        # ── Fetch attachments for inbound emails ──────────────────────────────
        # Each CUSTOMER_EMAIL episode has email_id → email_inbox_messages.email_inbox_id
        # → its attachments in email_message_attachments
        inbound_email_ids = [
            ep.email_id for ep in episodes
            if ep.email_id and ep.actor == "CUSTOMER"
        ]
        # Map email_id → list of (attachment_id, file_name, file_type)
        inbound_att_map: dict = {}
        if inbound_email_ids:
            try:
                from src.data.models.postgres.mailbox_models import (
                    EmailInboxMessage, EmailMessageAttachment,
                )
                result = await self.db.execute(
                    select(
                        EmailInboxMessage.email_inbox_id,
                        EmailMessageAttachment.attachment_id,
                        EmailMessageAttachment.file_name,
                        EmailMessageAttachment.file_type,
                    )
                    .join(
                        EmailMessageAttachment,
                        EmailMessageAttachment.message_id == EmailInboxMessage.message_id,
                    )
                    .where(EmailInboxMessage.email_inbox_id.in_(inbound_email_ids))
                )
                for row in result.fetchall():
                    inbound_att_map.setdefault(row.email_inbox_id, []).append(
                        TimelineAttachment(
                            attachment_id=row.attachment_id,
                            file_name=row.file_name,
                            file_type=row.file_type or "application/octet-stream",
                            download_url=f"/dispute/api/v1/inbox/attachments/{row.attachment_id}/download",
                            source="inbound",
                        )
                    )
            except Exception as exc:
                logger.warning(f"[dispute_id={dispute_id}] Failed to load inbound attachments: {exc}")

        # ── Fetch attachments for outbound emails (AI + Associate replies) ────
        # OutboundEmail.dispute_id = dispute_id; we match episode ↔ outbound by
        # closest created_at within a 30-second window.
        outbound_att_map: dict = {}   # episode_id → list[TimelineAttachment]
        outbound_episodes = [ep for ep in episodes if ep.actor in ("AI", "ASSOCIATE")]
        if outbound_episodes:
            try:
                from src.data.models.postgres.mailbox_models import (
                    OutboundEmail, OutboundEmailAttachment,
                )
                from sqlalchemy import func as safunc
                result = await self.db.execute(
                    select(
                        OutboundEmail.outbound_id,
                        OutboundEmail.created_at,
                        OutboundEmailAttachment.attachment_id,
                        OutboundEmailAttachment.file_name,
                        OutboundEmailAttachment.file_type,
                    )
                    .join(
                        OutboundEmailAttachment,
                        OutboundEmailAttachment.outbound_id == OutboundEmail.outbound_id,
                    )
                    .where(OutboundEmail.dispute_id == dispute_id)
                )
                outbound_rows = result.fetchall()

                if outbound_rows:
                    for ep in outbound_episodes:
                        for row in outbound_rows:
                            # Match if within 30 seconds of episode creation
                            diff = abs((row.created_at - ep.created_at).total_seconds())
                            if diff <= 30:
                                outbound_att_map.setdefault(ep.episode_id, []).append(
                                    TimelineAttachment(
                                        attachment_id=row.attachment_id,
                                        file_name=row.file_name,
                                        file_type=row.file_type or "application/octet-stream",
                                        download_url=f"/dispute/api/v1/outbound/attachments/{row.attachment_id}/download",
                                        source="outbound",
                                    )
                                )
            except Exception as exc:
                logger.warning(f"[dispute_id={dispute_id}] Failed to load outbound attachments: {exc}")

        # ── Fetch FA sender names for ASSOCIATE episodes ──────────────────────
        # Match each ASSOCIATE episode to the OutboundEmail sent within 30s,
        # then join users to get the real name.
        episode_actor_name: dict = {}   # episode_id → actor name string
        associate_episodes = [ep for ep in episodes if ep.actor == "ASSOCIATE"]
        if associate_episodes:
            try:
                from src.data.models.postgres.mailbox_models import OutboundEmail
                from src.data.models.postgres.user_models import User
                ob_result = await self.db.execute(
                    select(
                        OutboundEmail.outbound_id,
                        OutboundEmail.created_at,
                        OutboundEmail.sent_by_user_id,
                        User.name,
                    )
                    .join(User, User.user_id == OutboundEmail.sent_by_user_id)
                    .where(
                        OutboundEmail.dispute_id == dispute_id,
                        OutboundEmail.sent_by_user_id.isnot(None),
                    )
                )
                ob_rows = ob_result.fetchall()
                for ep in associate_episodes:
                    for row in ob_rows:
                        diff = abs((row.created_at - ep.created_at).total_seconds())
                        if diff <= 30:
                            episode_actor_name[ep.episode_id] = row.name
                            break
            except Exception as exc:
                logger.warning(f"[dispute_id={dispute_id}] Failed to load FA actor names: {exc}")

        # ── Build timeline ─────────────────────────────────────────────────────
        timeline = [
            TimelineEpisodeResponse(
                episode_id=ep.episode_id,
                actor=ep.actor,
                actor_name=episode_actor_name.get(ep.episode_id),
                episode_type=ep.episode_type,
                content_text=ep.content_text,
                created_at=ep.created_at,
                attachments=(
                    inbound_att_map.get(ep.email_id, [])
                    if ep.actor == "CUSTOMER"
                    else outbound_att_map.get(ep.episode_id, [])
                ),
            )
            for ep in episodes
        ]

        return DisputeTimelineResponse(
            dispute_id=dispute_id,
            customer_id=dispute.customer_id,
            status=dispute.status,
            timeline=timeline,
            pending_questions=len(pending_qs),
            assigned_to=active_assignment.assignee.email if active_assignment else None,
        )

    async def get_analysis(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        analysis = await self.analysis_repo.get_latest_for_dispute(dispute_id)
        if not analysis:
            raise AnalysisNotFoundError(dispute_id)
        return analysis

    async def reanalyze(self, dispute_id: int):
        dispute = await self.get_dispute(dispute_id)
        episodes = await self.ep_repo.get_latest_n(dispute_id, n=1)
        if not episodes or not episodes[0].email_id:
            raise AnalysisNotFoundError(dispute_id)

        ep = episodes[0]
        from src.control.tasks import process_email_task
        task = process_email_task.delay(
            email_id=ep.email_id,
            sender_email="reanalysis@system",
            subject="Reanalysis trigger",
            body_text=ep.content_text,
            attachment_texts=[],
        )
        return task.id

    async def get_episodes(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        return await self.ep_repo.get_episodes_for_dispute(dispute_id)

    async def get_summary(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        summary = await self.sum_repo.get_for_dispute(dispute_id)
        if not summary:
            raise SummaryNotFoundError(dispute_id)
        return summary

    async def get_open_questions(self, dispute_id: int):
        await self.get_dispute(dispute_id)
        return await self.q_repo.get_all_for_dispute(dispute_id)

    async def update_question_status(
        self, dispute_id: int, question_id: int, data: QuestionStatusUpdate, performed_by: int
    ):
        question = await self.q_repo.get_by_id(question_id)
        if not question or question.dispute_id != dispute_id:
            raise QuestionNotFoundError(question_id)

        question.status = data.status
        if data.status == "ANSWERED":
            question.answered_at = datetime.now(timezone.utc)
        await self.db.commit()
        return question

    async def create_fa_dispute(
        self,
        customer_id:      str,
        dispute_type_id:  int | None,
        custom_type_name: str | None,
        custom_type_desc: str | None,
        priority:         str,
        description:      str,
        invoice_id:       int | None,
        created_by:       int,
    ):
        """
        Create a dispute manually by a Finance Associate (no inbound email).
        If dispute_type_id is None, creates a new DisputeType from custom_type_name.
        """
        from src.data.models.postgres.dispute_models import DisputeType
        from src.data.models.postgres.models import DisputeActivityLog
        from sqlalchemy import select as sa_select_inner

        # ── Resolve or create dispute type ────────────────────────────────────
        if dispute_type_id:
            dtype = await self.dtype_repo.get_by_id(dispute_type_id)
            if not dtype:
                raise DisputeTypeNotFoundError(dispute_type_id)
        else:
            # Check if a type with this name already exists
            existing = (await self.db.execute(
                sa_select_inner(DisputeType).where(DisputeType.reason_name == custom_type_name)
            )).scalar_one_or_none()
            if existing:
                dtype = existing
            else:
                dtype = DisputeType(
                    reason_name=custom_type_name,
                    description=custom_type_desc or custom_type_name,
                    is_active=True,
                )
                self.db.add(dtype)
                await self.db.flush()

        # ── Create dispute ────────────────────────────────────────────────────
        from src.data.models.postgres.models import DisputeMaster as DisputeMasterModel
        import uuid
        dispute = DisputeMasterModel(
            email_id=None,         # FA-created — no source email
            customer_id=customer_id,
            dispute_type_id=dtype.dispute_type_id,
            invoice_id=invoice_id,
            status="OPEN",
            priority=priority,
            description=description,
            source="FA_MANUAL",
            dispute_token=None,   # assigned after flush once dispute_id is known
        )
        self.db.add(dispute)
        await self.db.flush()

        # Assign sequential PV- token now that dispute_id is known —
        # same format as agent-created disputes, source is never exposed in the token
        dispute.dispute_token = f"PV-{dispute.dispute_id:05d}"
        await self.db.flush()

        # ── Auto-assign to creating FA ────────────────────────────────────────
        from src.data.models.postgres.models import DisputeAssignment
        assignment = DisputeAssignment(
            dispute_id=dispute.dispute_id,
            assigned_to=created_by,
            status="ACTIVE",
        )
        self.db.add(assignment)

        # ── Activity log ──────────────────────────────────────────────────────
        self.db.add(DisputeActivityLog(
            dispute_id=dispute.dispute_id,
            action_type="CREATED",
            performed_by=created_by,
            notes=f"Dispute created manually by FA. Type: {dtype.reason_name}.",
        ))

        await self.db.commit()
        await self.db.refresh(dispute)
        logger.info(
            f"FA-created dispute: dispute_id={dispute.dispute_id} "
            f"customer={customer_id} type={dtype.reason_name} by user={created_by}"
        )
        return dispute

    async def get_enriched_list(
        self,
        status: str | None = None,
        priority: str | None = None,
        customer_id: str | None = None,
        assigned_to: int | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ):
        """
        Returns DisputeDetailResponse-ready dicts for all matched disputes
        in 5 parallel DB queries — no N+1 calls from the route layer.
        """
        from sqlalchemy import select as sa_select, and_ as sa_and_, func as sa_func
        from sqlalchemy.orm import joinedload
        from src.data.models.postgres.models import (
            DisputeAIAnalysis, DisputeAssignment, DisputeOpenQuestion,
        )
        from src.data.models.postgres.memory_models import DisputeMemoryEpisode
        from src.schemas.dispute_schemas import AIAnalysisResponse, DisputeDetailResponse

        items, total = await self.list_disputes(
            status=status, priority=priority, customer_id=customer_id,
            assigned_to=assigned_to, search=search, limit=limit, offset=offset,
        )
        if not items:
            return [], total

        id_list = [d.dispute_id for d in items]

        # ── Latest analysis per dispute ───────────────────────────────────────
        max_a_sq = (
            sa_select(
                DisputeAIAnalysis.dispute_id,
                sa_func.max(DisputeAIAnalysis.analysis_id).label("max_id"),
            )
            .where(DisputeAIAnalysis.dispute_id.in_(id_list))
            .group_by(DisputeAIAnalysis.dispute_id)
            .subquery()
        )
        analysis_map = {
            a.dispute_id: a
            for a in (await self.db.execute(
                sa_select(DisputeAIAnalysis).join(max_a_sq, sa_and_(
                    DisputeAIAnalysis.dispute_id == max_a_sq.c.dispute_id,
                    DisputeAIAnalysis.analysis_id == max_a_sq.c.max_id,
                ))
            )).scalars().all()
        }

        # ── Active assignment per dispute ─────────────────────────────────────
        assign_rows = (await self.db.execute(
            sa_select(DisputeAssignment)
            .options(joinedload(DisputeAssignment.assignee))
            .where(sa_and_(
                DisputeAssignment.dispute_id.in_(id_list),
                DisputeAssignment.status == "ACTIVE",
            ))
            .order_by(DisputeAssignment.assigned_at.desc())
        )).scalars().all()
        assign_map: dict = {}
        for a in assign_rows:
            if a.dispute_id not in assign_map:
                assign_map[a.dispute_id] = a

        # ── Pending question count per dispute ────────────────────────────────
        q_count_map = {
            row.dispute_id: row.cnt
            for row in (await self.db.execute(
                sa_select(
                    DisputeOpenQuestion.dispute_id,
                    sa_func.count(DisputeOpenQuestion.question_id).label("cnt"),
                )
                .where(sa_and_(
                    DisputeOpenQuestion.dispute_id.in_(id_list),
                    DisputeOpenQuestion.status == "PENDING",
                ))
                .group_by(DisputeOpenQuestion.dispute_id)
            )).all()
        }

        # ── has_new_customer_message per dispute — from dispute_new_message table ──
        from src.data.models.postgres.dispute_models import DisputeNewMessage
        new_msg_rows = (await self.db.execute(
            sa_select(DisputeNewMessage.dispute_id, DisputeNewMessage.has_new_message)
            .where(DisputeNewMessage.dispute_id.in_(id_list))
        )).all()
        ep_actor_map = {row.dispute_id: bool(row.has_new_message) for row in new_msg_rows}

        # ── Assemble ──────────────────────────────────────────────────────────
        enriched = []
        for d in items:
            raw_a = analysis_map.get(d.dispute_id)
            latest_analysis = None
            if raw_a:
                try:
                    latest_analysis = AIAnalysisResponse.model_validate(raw_a)
                except Exception:
                    pass
            active_assign = assign_map.get(d.dispute_id)
            try:
                enriched.append(DisputeDetailResponse(
                    dispute_id=d.dispute_id,
                    email_id=d.email_id,               # Optional[int] — None for FA-created
                    invoice_id=d.invoice_id,
                    payment_detail_id=d.payment_detail_id,
                    customer_id=d.customer_id,
                    dispute_type=d.dispute_type,
                    status=d.status,
                    priority=d.priority,
                    description=d.description,
                    created_at=d.created_at,
                    updated_at=d.updated_at,
                    latest_analysis=latest_analysis,
                    open_questions_count=q_count_map.get(d.dispute_id, 0),
                    assigned_to=active_assign.assignee.email if active_assign else None,
                    has_new_customer_message=ep_actor_map.get(d.dispute_id, False),
                ))
            except Exception as row_err:
                import logging as _log
                _log.getLogger(__name__).warning(
                    f"Skipping dispute_id={d.dispute_id} from list (validation error): {row_err}"
                )
        return enriched, total

    async def get_enriched_detail(self, dispute_id: int):
        """
        Returns a single DisputeDetailResponse with analysis, assignment,
        open question count and new-message flag — all from the service layer.
        """
        from src.schemas.dispute_schemas import AIAnalysisResponse, DisputeDetailResponse

        dispute = await self.get_dispute(dispute_id)

        latest_analysis = None
        try:
            raw = await self.analysis_repo.get_latest_for_dispute(dispute_id)
            if raw:
                latest_analysis = AIAnalysisResponse.model_validate(raw)
        except Exception:
            pass

        pending_qs    = await self.q_repo.get_all_for_dispute(dispute_id)
        active_assign = await self.assign_repo.get_active_assignment(dispute_id)

        has_new = False
        try:
            from src.data.repositories.dispute_repository import DisputeNewMessageRepository
            has_new = await DisputeNewMessageRepository(self.db).get_for_dispute(dispute_id)
        except Exception:
            pass

        return DisputeDetailResponse(
            dispute_id=dispute.dispute_id,
            email_id=dispute.email_id,
            invoice_id=dispute.invoice_id,
            payment_detail_id=dispute.payment_detail_id,
            customer_id=dispute.customer_id,
            dispute_type=dispute.dispute_type,
            status=dispute.status,
            priority=dispute.priority,
            description=dispute.description,
            created_at=dispute.created_at,
            updated_at=dispute.updated_at,
            latest_analysis=latest_analysis,
            open_questions_count=len([q for q in pending_qs if q.status == "PENDING"]),
            assigned_to=active_assign.assignee.email if active_assign else None,
            has_new_customer_message=has_new,
        )

    async def get_bulk_enriched(self, id_list: list[int]):
        """
        Fetch enriched detail for an explicit list of dispute IDs in one round-trip.
        Used by the bulk-detail endpoint.
        """
        from sqlalchemy import select as sa_select, and_ as sa_and_, func as sa_func
        from sqlalchemy.orm import joinedload, selectinload
        from src.data.models.postgres.models import (
            DisputeAIAnalysis, DisputeAssignment, DisputeOpenQuestion, DisputeMaster,
        )
        from src.data.models.postgres.memory_models import DisputeMemoryEpisode
        from src.schemas.dispute_schemas import AIAnalysisResponse, DisputeDetailResponse

        if not id_list:
            return []

        # Fetch disputes by explicit id_list
        disputes = list((await self.db.execute(
            sa_select(DisputeMaster)
            .options(selectinload(DisputeMaster.dispute_type))
            .where(DisputeMaster.dispute_id.in_(id_list))
            .order_by(DisputeMaster.created_at.desc())
        )).scalars().all())

        if not disputes:
            return []

        ids = [d.dispute_id for d in disputes]

        max_a_sq = (
            sa_select(DisputeAIAnalysis.dispute_id, sa_func.max(DisputeAIAnalysis.analysis_id).label("max_id"))
            .where(DisputeAIAnalysis.dispute_id.in_(ids)).group_by(DisputeAIAnalysis.dispute_id).subquery()
        )
        analysis_map = {a.dispute_id: a for a in (await self.db.execute(
            sa_select(DisputeAIAnalysis).join(max_a_sq, sa_and_(
                DisputeAIAnalysis.dispute_id == max_a_sq.c.dispute_id,
                DisputeAIAnalysis.analysis_id == max_a_sq.c.max_id,
            ))
        )).scalars().all()}

        assign_rows = (await self.db.execute(
            sa_select(DisputeAssignment).options(joinedload(DisputeAssignment.assignee))
            .where(sa_and_(DisputeAssignment.dispute_id.in_(ids), DisputeAssignment.status == "ACTIVE"))
            .order_by(DisputeAssignment.assigned_at.desc())
        )).scalars().all()
        assign_map: dict = {}
        for a in assign_rows:
            if a.dispute_id not in assign_map:
                assign_map[a.dispute_id] = a

        q_count_map = {row.dispute_id: row.cnt for row in (await self.db.execute(
            sa_select(DisputeOpenQuestion.dispute_id, sa_func.count(DisputeOpenQuestion.question_id).label("cnt"))
            .where(sa_and_(DisputeOpenQuestion.dispute_id.in_(ids), DisputeOpenQuestion.status == "PENDING"))
            .group_by(DisputeOpenQuestion.dispute_id)
        )).all()}

        # ── has_new_customer_message — from dispute_new_message table ───────────
        from src.data.models.postgres.dispute_models import DisputeNewMessage
        new_msg_rows2 = (await self.db.execute(
            sa_select(DisputeNewMessage.dispute_id, DisputeNewMessage.has_new_message)
            .where(DisputeNewMessage.dispute_id.in_(ids))
        )).all()
        ep_actor_map = {row.dispute_id: bool(row.has_new_message) for row in new_msg_rows2}

        results = []
        for d in disputes:
            raw_a = analysis_map.get(d.dispute_id)
            latest_analysis = None
            if raw_a:
                try:
                    latest_analysis = AIAnalysisResponse.model_validate(raw_a)
                except Exception:
                    pass
            active_assign = assign_map.get(d.dispute_id)
            results.append(DisputeDetailResponse(
                dispute_id=d.dispute_id, email_id=d.email_id, invoice_id=d.invoice_id,
                payment_detail_id=d.payment_detail_id, customer_id=d.customer_id,
                dispute_type=d.dispute_type, status=d.status, priority=d.priority,
                description=d.description, created_at=d.created_at, updated_at=d.updated_at,
                latest_analysis=latest_analysis,
                open_questions_count=q_count_map.get(d.dispute_id, 0),
                assigned_to=active_assign.assignee.email if active_assign else None,
                has_new_customer_message=ep_actor_map.get(d.dispute_id, False),
                source=getattr(d, "source", "EMAIL") or "EMAIL",
            ))
        return results
