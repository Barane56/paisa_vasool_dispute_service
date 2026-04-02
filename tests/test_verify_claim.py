"""
Unit tests for verify_claim node.
"""

import pytest

from src.control.agents.nodes.verify_claim import (
    _verify_contract_claim,
    _verify_payment_claim,
    _verify_pricing_claim,
)
from tests.conftest import (
    SAMPLE_INVOICE,
    SAMPLE_PAYMENT,
)


class TestVerifyPricingClaim:
    """Test cases for pricing claim verification."""

    @pytest.mark.asyncio
    async def test_pricing_match_customer_correct(self):
        """When invoice amount matches customer claim - customer is correct."""
        ar_docs = {
            "invoices": [{"doc_id": 1, "doc_type": "INVOICE"}],
            "contracts": [],
        }

        result = await _verify_pricing_claim(
            customer_claimed_amount="6820.00",
            customer_claimed_rate=None,
            invoice_details=SAMPLE_INVOICE,
            ar_docs=ar_docs,
        )

        assert result["status"] == "VERIFIED_CUSTOMER_CORRECT"
        assert result["recommendation"] == "ISSUE_CREDIT"

    @pytest.mark.asyncio
    async def test_pricing_mismatch_customer_incorrect(self):
        """When invoice amount differs from claim - customer is wrong."""
        ar_docs = {
            "invoices": [{"doc_id": 1, "doc_type": "INVOICE"}],
            "contracts": [],
        }

        result = await _verify_pricing_claim(
            customer_claimed_amount="5000.00",  # Different from invoice
            customer_claimed_rate=None,
            invoice_details=SAMPLE_INVOICE,
            ar_docs=ar_docs,
        )

        assert result["status"] == "VERIFIED_CUSTOMER_INCORRECT"
        assert result["recommendation"] == "NO_ACTION"

    @pytest.mark.asyncio
    async def test_pricing_with_contract(self):
        """When contract exists - needs manual verification."""
        ar_docs = {
            "invoices": [{"doc_id": 1, "doc_type": "INVOICE"}],
            "contracts": [{"doc_id": 2, "doc_type": "CONTRACT"}],
        }

        result = await _verify_pricing_claim(
            customer_claimed_amount="6200.00",
            customer_claimed_rate="45.00",
            invoice_details=SAMPLE_INVOICE,
            ar_docs=ar_docs,
        )

        # Contract exists but rate needs verification
        assert result["recommendation"] == "VERIFY_CONTRACT_RATE"


class TestVerifyPaymentClaim:
    """Test cases for payment claim verification."""

    @pytest.mark.asyncio
    async def test_payment_found_customer_incorrect(self):
        """When payment record exists - customer is wrong."""
        ar_docs = {
            "payments": [{"doc_id": 3, "doc_type": "PAYMENT"}],
        }

        result = await _verify_payment_claim(
            ar_docs=ar_docs,
            all_payment_details=[SAMPLE_PAYMENT],
        )

        assert result["status"] == "VERIFIED_CUSTOMER_INCORRECT"
        assert result["recommendation"] == "NO_ACTION"

    @pytest.mark.asyncio
    async def test_payment_not_found_inconclusive(self):
        """When no payment record - needs investigation."""
        ar_docs = {
            "payments": [],
        }

        result = await _verify_payment_claim(
            ar_docs=ar_docs,
            all_payment_details=[],
        )

        assert result["status"] == "INCONCLUSIVE"
        assert result["recommendation"] == "INVESTIGATE_PAYMENT"


class TestVerifyContractClaim:
    """Test cases for contract claim verification."""

    @pytest.mark.asyncio
    async def test_contract_found(self):
        """When contract found - needs rate extraction."""
        ar_docs = {
            "contracts": [{"doc_id": 2, "doc_type": "CONTRACT"}],
        }

        result = await _verify_contract_claim(
            customer_claimed_rate="45.00",
            ar_docs=ar_docs,
        )

        assert result["recommendation"] == "VERIFY_CONTRACT_RATE"

    @pytest.mark.asyncio
    async def test_contract_not_found(self):
        """When no contract - request copy."""
        ar_docs = {
            "contracts": [],
        }

        result = await _verify_contract_claim(
            customer_claimed_rate="45.00",
            ar_docs=ar_docs,
        )

        assert result["recommendation"] == "REQUEST_CONTRACT_COPY"
