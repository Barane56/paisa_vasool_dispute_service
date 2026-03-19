"""
src/core/services/draft_email_service.py
=========================================
Generates a professional AI email draft for a Finance Associate using Groq.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.data.repositories.repositories import MemoryEpisodeRepository
from src.handlers.http_clients.llm_client import get_llm_client

logger = logging.getLogger(__name__)


DRAFT_SYSTEM_PROMPT = """\
You are a professional Finance Associate at PaisaVasool, an accounts receivable company.
Your job is to write clear, professional, and empathetic email replies to customers regarding invoice disputes.

Guidelines:
- Be polite but firm and professional
- Address the customer's most recent concern directly
- Reference relevant facts from the conversation (amounts, invoice numbers, dates)
- The content being generated should be relevant to what the conversation is about.
- If no conversation is given, generate a simple template.
- Keep responses concise — under 200 words
- Do NOT write a subject line or salutation — write only the email body
- End with: Best regards,\nFinance Team, PaisaVasool
- Output ONLY the email body text, nothing else, no preamble
"""


async def generate_draft_email(
    dispute_id: int,
    db: AsyncSession,
    customer_id: str,
    dispute_type: Optional[str],
    status: str,
    priority: str,
    ai_summary: Optional[str],
    fa_name: Optional[str] = None,
) -> str:
    ep_repo  = MemoryEpisodeRepository(db)
    episodes = await ep_repo.get_episodes_for_dispute(dispute_id)

    transcript_lines = []
    for ep in episodes:
        role_map = {
            "CUSTOMER":  f"Customer ({customer_id})",
            "AI":        "AI Auto-Response",
            "ASSOCIATE": "Finance Associate",
            "SYSTEM":    "System",
        }
        role = role_map.get(ep.actor, ep.actor)
        dt   = ep.created_at.strftime("%d %b %Y %H:%M")
        transcript_lines.append(f"[{dt}] {role}:\n{ep.content_text}")

    transcript = "\n\n---\n\n".join(transcript_lines) if transcript_lines else "No conversation history yet."

    user_prompt = f"""\
Dispute Details:
- Dispute ID: #{dispute_id}
- Dispute Type: {dispute_type or 'Unknown'}
- Customer ID: {customer_id}
- Status: {status}
- Priority: {priority}
{f'- AI Summary: {ai_summary}' if ai_summary else ''}

Full Conversation History:
{transcript}

Write a professional email reply to the customer addressing their most recent concern.
"""

    llm = get_llm_client()
    draft = await llm.chat(
        prompt=user_prompt,
        system=DRAFT_SYSTEM_PROMPT,
        json_mode=False,
    )
    return draft.strip()
