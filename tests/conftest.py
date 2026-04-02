"""
Test fixtures for dispute resolution verification system.
"""

# Sample invoice data
SAMPLE_INVOICE = {
    "invoice_number": "INV-2024-001",
    "invoice_date": "2024-01-15",
    "due_date": "2024-02-14",
    "vendor_name": "Acme Services Ltd",
    "customer_name": "TechCorp Inc",
    "customer_id": "TC001",
    "line_items": [
        {
            "description": "Consulting Services - Phase 1",
            "quantity": 100,
            "unit_price": 50.00,
            "total": 5000.00,
        },
        {
            "description": "Software License - Annual",
            "quantity": 1,
            "unit_price": 1200.00,
            "total": 1200.00,
        },
    ],
    "subtotal": 6200.00,
    "tax_amount": 620.00,
    "total_amount": 6820.00,
    "currency": "USD",
    "payment_terms": "Net 30",
}

# Sample payment data
SAMPLE_PAYMENT = {
    "payment_reference": "PAY-2024-001",
    "payment_date": "2024-02-10",
    "amount_paid": 6820.00,
    "payment_mode": "Bank Transfer",
    "bank_reference": "BNK-REF-12345",
    "invoice_number": "INV-2024-001",
    "status": "COMPLETED",
}

# Sample contract text
SAMPLE_CONTRACT_TEXT = """
MASTER SERVICE AGREEMENT

This Agreement is entered into between Acme Services Ltd ("Provider") and
TechCorp Inc ("Customer") effective from January 1, 2024.

PRICING TERMS:
1. Consulting Services: $50.00 per hour
2. Software License: $1,200.00 per annum
3. Support Services: $25.00 per hour

PAYMENT TERMS:
- Net 30 days from invoice date
- Late payment interest: 1.5% per month

CONTRACT TERM:
- Effective: January 1, 2024
- Expiration: December 31, 2024
- Renewal: Auto-renewal unless cancelled 30 days prior

RATE ESCALATION:
- Annual escalation: 5% effective from second year

Signed: ________________
Date: January 1, 2024
"""

# Sample customer emails
CUSTOMER_EMAIL_CORRECT_PRICING = """
Subject: Pricing dispute - INV-2024-001

Dear Accounts Receivable,

We have received invoice INV-2024-001 for $6,820.00. However, according to our
contract (MSA-2024-001), the consulting rate should be $45.00 per hour, not
$50.00 as charged.
Please review and issue a corrected invoice.

Best regards,
John Smith
TechCorp Inc
"""

CUSTOMER_EMAIL_WRONG_PRICING = """
Subject: Question about invoice INV-2024-001

Dear Accounts Receivable,

We would like to confirm the pricing on invoice INV-2024-001. The total shows
$6,820.00 but our records indicate it should be $6,200.00.

Please clarify.

Thanks,
John Smith
TechCorp Inc
"""

CUSTOMER_EMAIL_PAYMENT_ALREADY_APPLIED = """
Subject: Payment already made - INV-2024-001

Dear Team,

We have already made payment for invoice INV-2024-001. The payment of $6,820.00
was made on February 10, 2024 via bank transfer (ref: BNK-REF-12345).

Please confirm receipt and update our account.

Regards,
John Smith
TechCorp Inc
"""

CUSTOMER_EMAIL_DUPLICATE = """
Subject: Duplicate charge on INV-2024-001

Hello,

We noticed that we have been charged twice for the same invoice INV-2024-001.
Please check and issue a credit note if necessary.

Thanks,
John Smith
TechCorp Inc
"""

# Expected verification results
EXPECTED_RESULTS = {
    "correct_pricing": {
        "claim_type": "PRICING",
        "verification_status": "VERIFIED_CUSTOMER_CORRECT",
        "resolution_action": "ISSUE_CREDIT",
        "can_auto_respond": False,
        "confidence_score": 0.9,
    },
    "wrong_pricing": {
        "claim_type": "PRICING",
        "verification_status": "VERIFIED_CUSTOMER_INCORRECT",
        "resolution_action": "NO_ACTION",
        "can_auto_respond": True,
        "confidence_score": 0.9,
    },
    "payment_claim": {
        "claim_type": "PAYMENT",
        "verification_status": "VERIFIED_CUSTOMER_INCORRECT",
        "resolution_action": "NO_ACTION",
        "can_auto_respond": True,
        "confidence_score": 0.85,
    },
    "duplicate": {
        "claim_type": "DUPLICATE",
        "verification_status": "INCONCLUSIVE",
        "resolution_action": "CHECK_CREDIT_NOTE",
        "can_auto_respond": False,
        "confidence_score": 0.6,
    },
}

# Sample AR documents
SAMPLE_AR_DOCUMENTS = [
    {
        "doc_id": 1,
        "doc_type": "INVOICE",
        "doc_date": "2024-01-15",
        "status": "ACTIVE",
        "raw_text": "Invoice INV-2024-001 for $6,820.00",
    },
    {
        "doc_id": 2,
        "doc_type": "CONTRACT",
        "doc_date": "2024-01-01",
        "status": "ACTIVE",
        "raw_text": SAMPLE_CONTRACT_TEXT,
    },
    {
        "doc_id": 3,
        "doc_type": "PAYMENT",
        "doc_date": "2024-02-10",
        "status": "ACTIVE",
        "raw_text": "Payment of $6,820.00 received",
    },
]

# FA Action suggestions
FA_SUGGESTION_EXAMPLES = {
    "ISSUE_CREDIT": {
        "action": "ISSUE_CREDIT",
        "suggestion": "Customer overcharged by ₹6820. "
        "Recommend issuing credit note for amount ₹6820.",
        "reasoning": "Invoice amount exceeds contracted rate. Evidence: "
        "Contract shows $50/hr, invoice charged $50/hr but customer claims $45/hr.",
    },
    "VERIFY_CONTRACT_RATE": {
        "action": "VERIFY_CONTRACT_RATE",
        "suggestion": "Contract rate needs verification. "
        "FA to compare contract clause with invoice line items.",
        "reasoning": "Contract found but rate ambiguity. "
        "Need FA to manually compare contract clause with invoice line.",
    },
    "NO_ACTION": {
        "action": "NO_ACTION",
        "suggestion": "Our records are correct. No action needed - "
        "respond to customer with evidence.",
        "reasoning": "Verification complete: our records match the invoice.",
    },
    "COMPARE_PO_GRN": {
        "action": "COMPARE_PO_GRN",
        "suggestion": "Delivery quantity dispute. "
        "FA to compare PO qty 100 vs GRN qty 100.",
        "reasoning": "Customer claims delivery issue. Need PO-GRN reconciliation.",
    },
}
