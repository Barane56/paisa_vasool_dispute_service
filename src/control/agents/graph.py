"""
src/control/agents/graph.py
============================
Builds and runs the LangGraph email processing pipeline.

Pipeline order:
  extract_text
      ↓
  extract_invoice_data_via_groq
      ↓
  identify_invoice
      ↓
  classify_email          ← before fetch_context so dispute_type is known for lookup
      ↓
  fetch_context           ← 4-level dispute lookup + memory load
      ↓
  embed_and_search        ← cold mail path only; pgvector similarity search
      ↓
  resolve_dispute_link    ← scenario A/B/C routing
      ↓
  generate_ai_response
      ↓
  persist_results
"""

from __future__ import annotations
from functools import partial
from typing import List

from langgraph.graph import StateGraph, END

from src.control.agents.state import EmailProcessingState, build_initial_state
from src.control.agents.nodes import (
    node_extract_text,
    node_extract_invoice_data_via_groq,
    node_identify_invoice,
    node_classify_email,
    node_fetch_context,
    node_embed_and_search,
    node_resolve_dispute_link,
    node_generate_ai_response,
    node_persist_results,
)
from src.observability import observe, langfuse_context


def build_email_processing_graph(db_session=None, llm_client=None):
    graph = StateGraph(EmailProcessingState)

    graph.add_node("extract_text",                 node_extract_text)
    graph.add_node("extract_invoice_data_via_groq", partial(node_extract_invoice_data_via_groq, llm_client=llm_client))
    graph.add_node("identify_invoice",              partial(node_identify_invoice,              db_session=db_session))
    graph.add_node("classify_email",                partial(node_classify_email,                llm_client=llm_client, db_session=db_session))
    graph.add_node("fetch_context",                 partial(node_fetch_context,                 db_session=db_session))
    graph.add_node("embed_and_search",              partial(node_embed_and_search,              llm_client=llm_client, db_session=db_session))
    graph.add_node("resolve_dispute_link",          partial(node_resolve_dispute_link,          db_session=db_session))
    graph.add_node("generate_ai_response",          partial(node_generate_ai_response,          llm_client=llm_client))
    graph.add_node("persist_results",               partial(node_persist_results,               db_session=db_session))

    graph.set_entry_point("extract_text")
    graph.add_edge("extract_text",                 "extract_invoice_data_via_groq")
    graph.add_edge("extract_invoice_data_via_groq","identify_invoice")
    graph.add_edge("identify_invoice",             "classify_email")
    graph.add_edge("classify_email",               "fetch_context")
    graph.add_edge("fetch_context",                "embed_and_search")
    graph.add_edge("embed_and_search",             "resolve_dispute_link")
    graph.add_edge("resolve_dispute_link",         "generate_ai_response")
    graph.add_edge("generate_ai_response",         "persist_results")
    graph.add_edge("persist_results",              END)

    return graph.compile()


@observe(name="run_email_processing")
async def run_email_processing(
    email_id:         int,
    sender_email:     str,
    subject:          str,
    body_text:        str,
    attachment_texts: List[str],
    db_session=None,
    llm_client=None,
) -> EmailProcessingState:
    # Tag the top-level trace so every node span nests under email_id in Langfuse
    langfuse_context.update_current_trace(
        name=f"email_processing:{email_id}",
        session_id=str(email_id),
        tags=["email_processing"],
        metadata={"sender_email": sender_email, "subject": subject},
    )

    graph         = build_email_processing_graph(db_session=db_session, llm_client=llm_client)
    initial_state = build_initial_state(email_id, sender_email, subject, body_text, attachment_texts)
    result        = await graph.ainvoke(initial_state)

    # Surface final outcome on the trace for quick filtering in Langfuse
    langfuse_context.update_current_trace(
        metadata={
            "dispute_id":            result.get("dispute_id"),
            "auto_response":         result.get("auto_response_generated"),
            "dispute_type":          result.get("dispute_type_name"),
            "confidence_score":      result.get("confidence_score"),
            "error":                 result.get("error"),
        }
    )

    return result
