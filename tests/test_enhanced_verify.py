"""
Unit tests for enhanced_verify node.
"""

import pytest

from src.control.agents.nodes.enhanced_verify import (
    _build_fa_suggestion,
    _check_credit_notes,
    _extract_contract_rates,
    _match_po_grn,
)
from tests.conftest import (
    SAMPLE_CONTRACT_TEXT,
)


class TestExtractContractRates:
    """Test contract rate extraction from contract text."""

    @pytest.mark.asyncio
    async def test_extract_contract_rates_success(self):
        """Test successful rate extraction from contract."""
        # This would need a real LLM client to test fully
        result = await _extract_contract_rates(
            llm_client=None,  # Will return fallback
            contract_text=SAMPLE_CONTRACT_TEXT,
            invoice_number="INV-2024-001",
            invoice_amount="6820.00",
        )

        # Without LLM client, returns fallback
        assert result["contract_rate"] is None
        assert result["comparison_result"] == "NEED_REVIEW"


class TestCreditNoteCheck:
    """Test credit note detection."""

    @pytest.mark.asyncio
    async def test_credit_note_check_no_db(self):
        """Test credit note check without DB session."""
        result = await _check_credit_notes(
            db_session=None,
            invoice_number="INV-2024-001",
            customer_scope="test@example.com",
        )

        assert result["has_credit_note"] is False


class TestPOGRNMatch:
    """Test PO-GRN matching."""

    @pytest.mark.asyncio
    async def test_po_grn_match_no_db(self):
        """Test PO-GRN match without DB session."""
        result = await _match_po_grn(
            db_session=None,
            invoice_number="INV-2024-001",
            customer_scope="test@example.com",
        )

        assert result["po_found"] is False
        assert result["grn_found"] is False


class TestFASuggestion:
    """Test FA action suggestion generation."""

    def test_build_issue_credit_suggestion(self):
        """Test ISSUE_CREDIT suggestion format."""
        result = _build_fa_suggestion(
            "ISSUE_CREDIT",
            {"amount": "6820", "evidence": "Contract rate mismatch"},
        )

        assert result["action"] == "ISSUE_CREDIT"
        assert "6820" in result["suggestion"]
        assert "reasoning" in result

    def test_build_verify_contract_suggestion(self):
        """Test VERIFY_CONTRACT_RATE suggestion format."""
        result = _build_fa_suggestion(
            "VERIFY_CONTRACT_RATE",
            {"amount": "0", "evidence": "Ambiguous"},
        )

        assert result["action"] == "VERIFY_CONTRACT_RATE"

    def test_build_no_action_suggestion(self):
        """Test NO_ACTION suggestion format."""
        result = _build_fa_suggestion(
            "NO_ACTION",
            {"amount": "0", "evidence": "Records match"},
        )

        assert result["action"] == "NO_ACTION"
        assert "Our records are correct" in result["suggestion"]
