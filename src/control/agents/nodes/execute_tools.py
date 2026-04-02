"""
src/control/agents/nodes/execute_tools.py
=========================================
NEW NODE - Industry-standard tool execution with verification.

This node runs AFTER verify_claim and parses tool proposals from the LLM,
executes them with verification, and returns receipts.

Flow:
  1. Parse tool_proposals from LLM response (in generate_response)
  2. Verify each proposal is valid
  3. Execute tools with database session
  4. Collect receipts
  5. Update state with results
"""

from __future__ import annotations

import logging

from src.control.agents.state import EmailProcessingState
from src.control.agents.tool_executor import (
    execute_tool_proposals,
    render_tool_results,
    verify_proposal,
)
from src.observability import langfuse_context, observe

logger = logging.getLogger(__name__)


@observe(name="node_execute_tools")
async def node_execute_tools(
    state: EmailProcessingState, db_session=None
) -> EmailProcessingState:
    """
    Execute tool proposals from LLM response.
    Runs after verify_claim to take actions based on verification result.
    """
    email_id = state["email_id"]
    tool_proposals = state.get("tool_proposals", [])

    # If no proposals, skip tool execution
    if not tool_proposals:
        logger.info(f"[email_id={email_id}] No tool proposals to execute")
        return {
            **state,
            "tool_receipts": [],
            "executed_tool_count": 0,
        }

    # If no db_session, just validate proposals without executing
    if not db_session:
        logger.warning(
            f"[email_id={email_id}] No db_session, validating proposals only"
        )
        validated_receipts = []
        for prop in tool_proposals:
            error = verify_proposal(prop)
            validated_receipts.append(
                {
                    "tool": prop.get("name", "unknown"),
                    "ok": not bool(error),
                    "ref": "validation-only",
                    "message": error or "validated",
                    "idempotency_key": prop.get("idempotency_key"),
                }
            )
        return {
            **state,
            "tool_receipts": validated_receipts,
            "executed_tool_count": 0,
        }

    # Execute tools
    try:
        receipts = await execute_tool_proposals(
            proposals=tool_proposals,
            db_session=db_session,
        )

        # Convert receipts to dict for state
        receipt_dicts = [
            {
                "tool": r.tool,
                "ok": r.ok,
                "ref": r.ref,
                "message": r.message,
                "data": r.data,
                "idempotency_key": r.idempotency_key,
                "executed_at": r.executed_at,
            }
            for r in receipts
        ]

        success_count = sum(1 for r in receipts if r.ok)
        logger.info(
            f"[email_id={email_id}] Tool execution: "
            f"{success_count}/{len(receipts)} succeeded"
        )

        # Render results for logging
        result_summary = render_tool_results(tool_proposals, receipts)
        logger.info(f"[email_id={email_id}] Tool results:\n{result_summary}")

        # Update langfuse trace
        langfuse_context.update_current_observation(
            output={
                "tool_count": len(tool_proposals),
                "success_count": success_count,
                "receipts": receipt_dicts,
            }
        )

        return {
            **state,
            "tool_receipts": receipt_dicts,
            "executed_tool_count": success_count,
        }

    except Exception as e:
        logger.error(f"[email_id={email_id}] Tool execution failed: {e}", exc_info=True)
        return {
            **state,
            "tool_receipts": [],
            "executed_tool_count": 0,
            "error": f"Tool execution error: {e}",
        }
