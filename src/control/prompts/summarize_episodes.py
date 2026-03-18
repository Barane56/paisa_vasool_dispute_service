"""
src/control/prompts/summarize_episodes.py
"""
from pathlib import Path
from typing import List, Dict, Optional
from poml import poml as render_poml

PROMPT_NAME    = "summarize_episodes"
PROMPT_VERSION = "1.0"
_TEMPLATE = str(Path(__file__).parent / "templates" / "summarize_episodes.poml")


def build_summarize_episodes_prompt(
    episodes: List[Dict],
    existing_summary: Optional[str] = None,
) -> str:
    episodes_text = "\n".join([
        f"[{ep.get('actor', 'UNKNOWN')}] {ep.get('content_text', '')[:300]}"
        for ep in episodes
    ])

    context = {
        "episodes_text":    episodes_text,
        "existing_summary": existing_summary or "",
    }

    messages = render_poml(_TEMPLATE, context)
    return "\n\n".join(m["content"] for m in messages if m.get("content"))
