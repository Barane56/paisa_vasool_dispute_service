"""
seed_data.py
============
Run this script to:
  1. Generate 14 realistic customer email PDFs (saved to ./sample_emails/)
  2. Seed the database with matching InvoiceData and PaymentDetail records
     – multiple payments per invoice are fully supported

Usage:
    python seed_data.py

Requirements:
    pip install reportlab asyncpg sqlalchemy asyncio

Make sure your .env DATABASE_URL is set, or edit DATABASE_URL below directly.
"""

import asyncio
import os
import json
from datetime import datetime, timezone
from pathlib import Path

# ── PDF generation ────────────────────────────────────────────────────────────
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# ── DB ────────────────────────────────────────────────────────────────────────
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/auth_db",
)

OUTPUT_DIR = Path("sample_emails")
OUTPUT_DIR.mkdir(exist_ok=True)

styles = getSampleStyleSheet()

# =============================================================================
# DEFAULT DISPUTE TYPES  (10 seed types — AI dynamically adds more on the go)
# =============================================================================

DISPUTE_TYPES = [
    {
        "reason_name":    "Pricing Mismatch",
        "description":    "Customer claims the price charged differs from the agreed or quoted price.",
        "severity_level": "HIGH",
    },
    {
        "reason_name":    "Short Payment",
        "description":    "Customer has paid less than the invoiced amount without prior agreement.",
        "severity_level": "HIGH",
    },
    {
        "reason_name":    "Duplicate Invoice",
        "description":    "Customer believes they have been billed twice for the same goods or services.",
        "severity_level": "HIGH",
    },
    {
        "reason_name":    "Tax Dispute",
        "description":    "Customer disputes the tax rate, tax amount, or tax exemption applied on the invoice.",
        "severity_level": "MEDIUM",
    },
    {
        "reason_name":    "Payment Not Reflected",
        "description":    "Customer claims payment was made but it has not been applied to the invoice on record.",
        "severity_level": "HIGH",
    },
    {
        "reason_name":    "Goods Not Received",
        "description":    "Customer disputes the invoice because the goods or services were not delivered.",
        "severity_level": "HIGH",
    },
    {
        "reason_name":    "Quality Dispute",
        "description":    "Customer received goods or services but disputes the quality or completeness of delivery.",
        "severity_level": "MEDIUM",
    },
    {
        "reason_name":    "Early Payment Discount",
        "description":    "Customer claims an early payment discount was applicable but was not reflected on the invoice.",
        "severity_level": "MEDIUM",
    },
    {
        "reason_name":    "General Clarification",
        "description":    "General inquiries and clarification requests that do not constitute a formal dispute.",
        "severity_level": "LOW",
    },
    {
        "reason_name":    "Payment Terms Dispute",
        "description":    "Customer disputes the payment due date, credit period, or agreed payment terms on the invoice.",
        "severity_level": "MEDIUM",
    },
]

# =============================================================================
# INVOICES  (10 diverse invoices across industries / currencies)
# =============================================================================

INVOICES = [
    # 1. Office furniture — Acme Corp
    {
        "invoice_number": "INV-2024-001",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-001.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-001",
            "invoice_date": "2024-11-01",
            "due_date": "2024-11-30",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Acme Corp",
            "customer_id": "acme",
            "line_items": [
                {"description": "Office Furniture Set",  "qty": 10, "unit_price": 5000.00, "total": 50000.00},
                {"description": "Ergonomic Chairs",      "qty": 20, "unit_price": 2500.00, "total": 50000.00},
            ],
            "subtotal": 100000.00,
            "tax_amount": 18000.00,
            "total_amount": 118000.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-ACME-2024-011",
        },
    },
    # 2. Software licence — TechSoft Solutions
    {
        "invoice_number": "INV-2024-002",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-002.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-002",
            "invoice_date": "2024-11-05",
            "due_date": "2024-12-05",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "TechSoft Solutions",
            "customer_id": "techsoft",
            "line_items": [
                {"description": "Annual Software License",         "qty": 1,  "unit_price": 250000.00, "total": 250000.00},
                {"description": "Implementation Support (hrs)",    "qty": 40, "unit_price": 3000.00,   "total": 120000.00},
            ],
            "subtotal": 370000.00,
            "tax_amount": 66600.00,
            "total_amount": 436600.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-TS-2024-089",
        },
    },
    # 3. Industrial equipment — Global Traders
    {
        "invoice_number": "INV-2024-003",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-003.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-003",
            "invoice_date": "2024-11-10",
            "due_date": "2024-12-10",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Global Traders Ltd",
            "customer_id": "globaltraders",
            "line_items": [
                {"description": "Industrial Equipment - Model X200", "qty": 5, "unit_price": 80000.00, "total": 400000.00},
                {"description": "Spare Parts Kit",                   "qty": 5, "unit_price": 12000.00, "total": 60000.00},
                {"description": "Installation Service",              "qty": 1, "unit_price": 25000.00, "total": 25000.00},
            ],
            "subtotal": 485000.00,
            "tax_amount": 87300.00,
            "total_amount": 572300.00,
            "currency": "INR",
            "payment_terms": "Net 45",
            "po_reference": "PO-GT-2024-033",
        },
    },
    # 4. POS systems — Sunrise Retail
    {
        "invoice_number": "INV-2024-004",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-004.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-004",
            "invoice_date": "2024-11-15",
            "due_date": "2024-12-15",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Sunrise Retail Pvt Ltd",
            "customer_id": "sunrise",
            "line_items": [
                {"description": "Point of Sale Systems",      "qty": 15, "unit_price": 35000.00, "total": 525000.00},
                {"description": "Annual Maintenance Contract","qty": 1,  "unit_price": 45000.00, "total": 45000.00},
            ],
            "subtotal": 570000.00,
            "tax_amount": 102600.00,
            "total_amount": 672600.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-SR-2024-077",
        },
    },
    # 5. Fleet management — Metro Logistics
    {
        "invoice_number": "INV-2024-005",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-005.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-005",
            "invoice_date": "2024-11-20",
            "due_date": "2024-12-20",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Metro Logistics",
            "customer_id": "metro",
            "line_items": [
                {"description": "Fleet Management Software", "qty": 1,  "unit_price": 180000.00, "total": 180000.00},
                {"description": "GPS Devices",               "qty": 50, "unit_price": 4500.00,   "total": 225000.00},
            ],
            "subtotal": 405000.00,
            "tax_amount": 72900.00,
            "total_amount": 477900.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "CONTRACT-METRO-2024-001",
        },
    },
    # 6. Cloud infrastructure — FinTech Nexus (USD)
    {
        "invoice_number": "INV-2024-006",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-006.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-006",
            "invoice_date": "2024-11-25",
            "due_date": "2024-12-25",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "FinTech Nexus Inc",
            "customer_id": "fintechnexus",
            "line_items": [
                {"description": "Cloud Infra Compute (Oct)",   "qty": 1, "unit_price": 8400.00,  "total": 8400.00},
                {"description": "Cloud Infra Storage (Oct)",   "qty": 1, "unit_price": 1200.00,  "total": 1200.00},
                {"description": "Managed Security Services",   "qty": 1, "unit_price": 3500.00,  "total": 3500.00},
                {"description": "SLA Premium Support",         "qty": 1, "unit_price": 1500.00,  "total": 1500.00},
            ],
            "subtotal": 14600.00,
            "tax_amount": 0.00,
            "total_amount": 14600.00,
            "currency": "USD",
            "payment_terms": "Net 15",
            "po_reference": "PO-FN-2024-US-044",
            "notes": "Tax exempt — export of services",
        },
    },
    # 7. Pharmaceutical raw materials — MedPlus Labs
    {
        "invoice_number": "INV-2024-007",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-007.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-007",
            "invoice_date": "2024-12-01",
            "due_date": "2024-12-31",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "MedPlus Laboratories Pvt Ltd",
            "customer_id": "medplus",
            "line_items": [
                {"description": "Paracetamol API (kg)",           "qty": 500,  "unit_price": 320.00,  "total": 160000.00},
                {"description": "Ibuprofen API (kg)",             "qty": 200,  "unit_price": 580.00,  "total": 116000.00},
                {"description": "Microcrystalline Cellulose (kg)","qty": 1000, "unit_price": 95.00,   "total": 95000.00},
                {"description": "Cold-chain Packaging",           "qty": 1,    "unit_price": 18000.00,"total": 18000.00},
            ],
            "subtotal": 389000.00,
            "tax_amount": 19450.00,
            "total_amount": 408450.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "batch_numbers": ["BATCH-API-2024-112", "BATCH-API-2024-113"],
            "po_reference": "PO-MPL-2024-201",
        },
    },
    # 8. Construction materials — BuildRight Infra
    {
        "invoice_number": "INV-2024-008",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-008.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-008",
            "invoice_date": "2024-12-05",
            "due_date": "2025-01-04",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "BuildRight Infrastructure Ltd",
            "customer_id": "buildright",
            "line_items": [
                {"description": "TMT Steel Bars Fe500D (MT)",        "qty": 50,   "unit_price": 55000.00, "total": 2750000.00},
                {"description": "Portland Cement 53 Grade (bag)",    "qty": 5000, "unit_price": 380.00,   "total": 1900000.00},
                {"description": "Aggregates 20mm (MT)",              "qty": 200,  "unit_price": 1200.00,  "total": 240000.00},
                {"description": "Transportation & Freight",          "qty": 1,    "unit_price": 85000.00, "total": 85000.00},
            ],
            "subtotal": 4975000.00,
            "tax_amount": 894750.00,
            "total_amount": 5869750.00,
            "currency": "INR",
            "payment_terms": "50% advance, balance Net 60",
            "po_reference": "PO-BRI-2024-BR-008",
        },
    },
    # 9. Renewable energy equipment — GreenPower Solutions
    {
        "invoice_number": "INV-2024-009",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-009.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-009",
            "invoice_date": "2024-12-10",
            "due_date": "2025-01-09",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "GreenPower Solutions Pvt Ltd",
            "customer_id": "greenpower",
            "line_items": [
                {"description": "Solar Panels 400W Mono PERC (units)", "qty": 200, "unit_price": 12500.00,  "total": 2500000.00},
                {"description": "String Inverter 50kW",                "qty": 4,   "unit_price": 185000.00, "total": 740000.00},
                {"description": "Mounting Structure (set)",             "qty": 200, "unit_price": 1800.00,   "total": 360000.00},
                {"description": "DC & AC Cables (lot)",                 "qty": 1,   "unit_price": 95000.00,  "total": 95000.00},
                {"description": "EPC Project Management",               "qty": 1,   "unit_price": 120000.00, "total": 120000.00},
            ],
            "subtotal": 3815000.00,
            "tax_amount": 458850.00,
            "total_amount": 4273850.00,
            "currency": "INR",
            "payment_terms": "30% advance, 40% on delivery, 30% on commissioning",
            "project_id": "GP-SOLAR-RAJASTHAN-2024",
            "po_reference": "PO-GPS-2024-SOLAR-009",
        },
    },
    # 10. Hospitality supplies — Grand Meridian Hotels
    {
        "invoice_number": "INV-2024-010",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-010.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-010",
            "invoice_date": "2024-12-15",
            "due_date": "2025-01-14",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Grand Meridian Hotels & Resorts",
            "customer_id": "grandmeridian",
            "line_items": [
                {"description": "Egyptian Cotton Bed Linen Set (pcs)", "qty": 500, "unit_price": 2400.00, "total": 1200000.00},
                {"description": "Bathroom Amenities Kit",              "qty": 2000,"unit_price": 350.00,  "total": 700000.00},
                {"description": "In-Room Beverage Supplies (case)",    "qty": 300, "unit_price": 1800.00, "total": 540000.00},
                {"description": "Laundry & Housekeeping Chemicals",    "qty": 1,   "unit_price": 95000.00,"total": 95000.00},
                {"description": "Branded Stationery (box)",            "qty": 100, "unit_price": 1200.00, "total": 120000.00},
            ],
            "subtotal": 2655000.00,
            "tax_amount": 477900.00,
            "total_amount": 3132900.00,
            "currency": "INR",
            "payment_terms": "Net 45",
            "po_reference": "PO-GMH-2024-Q4-010",
        },
    },

    # 11. Acme Corp — Networking equipment (clean invoice, fully paid)
    {
        "invoice_number": "INV-2024-011",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-011.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-011",
            "invoice_date": "2026-01-15",
            "due_date": "2026-02-14",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Acme Corp",
            "customer_id": "acme",
            "billing_address": "Acme Corp, Powai, Mumbai — 400 076",
            "gstin": "27AABCA1234F1Z5",
            "line_items": [
                {"description": "Network Switch 48-Port (Cisco Catalyst 2960)", "qty": 4, "unit_price": 85000.00, "total": 340000.00, "hsn": "8517"},
                {"description": "Patch Cables CAT6 (box of 50)", "qty": 10, "unit_price": 3500.00, "total": 35000.00},
                {"description": "Rack Mounting Hardware (set)", "qty": 4, "unit_price": 2500.00, "total": 10000.00},
            ],
            "subtotal": 385000.00,
            "tax_rate_pct": 18,
            "tax_amount": 69300.00,
            "total_amount": 454300.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-ACME-2026-001",
        },
    },

    # 12. Acme Corp — IT Equipment, GST exemption not applied (HSN 8471)
    {
        "invoice_number": "INV-2024-012",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-012.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-012",
            "invoice_date": "2026-02-20",
            "due_date": "2026-03-22",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Acme Corp",
            "customer_id": "acme",
            "billing_address": "Acme Corp, Andheri East, Mumbai — 400 069",
            "gstin": "27AABCA1234F1Z5",
            "line_items": [
                {"description": "Laptop Dell Latitude 5540 (HSN 8471)", "qty": 10, "unit_price": 82000.00, "total": 820000.00, "hsn": "8471"},
                {"description": "Laptop Docking Station (HSN 8471)", "qty": 10, "unit_price": 8000.00, "total": 80000.00, "hsn": "8471"},
                {"description": "Extended Warranty 3-Year", "qty": 10, "unit_price": 5000.00, "total": 50000.00},
            ],
            "subtotal": 950000.00,
            "gst_exemption_note": "Customer holds exemption cert GSTIN-EX-MH-2024-00881 for HSN 8471 goods. GST charged at 18% — NOT exempt. Disputed amount: INR 1,62,000.",
            "tax_rate_pct": 18,
            "tax_amount_charged": 171000.00,
            "tax_amount_if_exempt": 9000.00,
            "disputed_tax_amount": 162000.00,
            "total_amount": 1121000.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-ACME-2026-002",
            "status": "DISPUTED",
        },
    },

    # 13. Acme Corp — Consulting services, wrong billing address on invoice
    {
        "invoice_number": "INV-2024-013",
        "invoice_url": "https://storage.example.com/invoices/INV-2024-013.pdf",
        "invoice_details": {
            "invoice_number": "INV-2024-013",
            "invoice_date": "2026-02-25",
            "due_date": "2026-03-27",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Acme Corp",
            "customer_id": "acme",
            "billing_address_on_invoice": "Acme Corp, Andheri East, Mumbai — 400 069",
            "correct_billing_address": "Acme Corp, Powai, Mumbai — 400 076",
            "gstin": "27AABCA1234F1Z5",
            "address_note": "Address updated to Powai per GSTIN records since January 2026. Invoice shows old address — must be reissued for ITC claim.",
            "line_items": [
                {"description": "IT Strategy Consulting Q1 2026 (SAC 998314)", "qty": 80, "unit_price": 5500.00, "total": 440000.00, "sac": "998314"},
                {"description": "Digital Transformation Roadmap Report", "qty": 1, "unit_price": 75000.00, "total": 75000.00},
                {"description": "Workshop Facilitation (2 days)", "qty": 2, "unit_price": 35000.00, "total": 70000.00},
            ],
            "subtotal": 585000.00,
            "tax_rate_pct": 18,
            "tax_amount": 105300.00,
            "total_amount": 690300.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-ACME-2026-003",
            "status": "ON_HOLD",
            "hold_reason": "Wrong billing address — reissue required before payment.",
        },
    },
]

# =============================================================================
# PAYMENTS  (multiple payments per invoice)
# =============================================================================

PAYMENTS = [
    # INV-2024-001 — Acme: single full payment
    {
        "customer_id": "acme",
        "invoice_number": "INV-2024-001",
        "payment_url": "https://storage.example.com/payments/PAY-2024-001A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-001A",
            "payment_date": "2024-11-25",
            "amount_paid": 118000.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT24325001234",
            "invoice_number": "INV-2024-001",
            "customer_id": "acme",
            "status": "CLEARED",
            "payment_type": "FULL",
            "payment_sequence": 1,
        },
    },
    # INV-2024-002 — TechSoft: first partial payment (short payment dispute)
    {
        "customer_id": "techsoft",
        "invoice_number": "INV-2024-002",
        "payment_url": "https://storage.example.com/payments/PAY-2024-002A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-002A",
            "payment_date": "2024-11-30",
            "amount_paid": 400000.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS24330005678",
            "invoice_number": "INV-2024-002",
            "customer_id": "techsoft",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 1,
            "note": "First instalment — customer disputes INR 36,600 balance over implementation hours rate",
        },
    },
    # INV-2024-002 — TechSoft: second payment (pending — balance in dispute)
    {
        "customer_id": "techsoft",
        "invoice_number": "INV-2024-002",
        "payment_url": "https://storage.example.com/payments/PAY-2024-002B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-002B",
            "payment_date": None,
            "amount_paid": 36600.00,
            "payment_mode": "NEFT",
            "bank_reference": None,
            "invoice_number": "INV-2024-002",
            "customer_id": "techsoft",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance payment held pending dispute resolution on implementation hours billing",
        },
    },
    # INV-2024-003 — GlobalTraders: full payment with GST dispute note
    {
        "customer_id": "globaltraders",
        "invoice_number": "INV-2024-003",
        "payment_url": "https://storage.example.com/payments/PAY-2024-003A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-003A",
            "payment_date": "2024-12-08",
            "amount_paid": 572300.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT24342009012",
            "invoice_number": "INV-2024-003",
            "customer_id": "globaltraders",
            "status": "CLEARED",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Paid in full as goodwill — requesting credit note INR 24,000 for GST calculation error",
        },
    },
    # INV-2024-004 — Sunrise Retail: 50% advance paid
    {
        "customer_id": "sunrise",
        "invoice_number": "INV-2024-004",
        "payment_url": "https://storage.example.com/payments/PAY-2024-004A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-004A",
            "payment_date": "2024-11-20",
            "amount_paid": 336300.00,
            "payment_mode": "IMPS",
            "bank_reference": "IMPS24324007721",
            "invoice_number": "INV-2024-004",
            "customer_id": "sunrise",
            "status": "CLEARED",
            "payment_type": "ADVANCE",
            "payment_sequence": 1,
            "note": "50% advance per PO terms — balance withheld pending quantity dispute resolution",
        },
    },
    # INV-2024-004 — Sunrise Retail: balance payment failed (quantity dispute)
    {
        "customer_id": "sunrise",
        "invoice_number": "INV-2024-004",
        "payment_url": "https://storage.example.com/payments/PAY-2024-004B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-004B",
            "payment_date": "2024-12-12",
            "amount_paid": 336300.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT24346011894",
            "invoice_number": "INV-2024-004",
            "customer_id": "sunrise",
            "status": "FAILED",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "failure_reason": "Customer withheld payment — 3 POS units not delivered per GRN-SR-2024-088",
            "note": "Payment blocked — quantity on invoice (15 POS units) vs delivery note (12 units)",
        },
    },
    # INV-2024-006 — FinTech Nexus (USD): compute portion cleared
    {
        "customer_id": "fintechnexus",
        "invoice_number": "INV-2024-006",
        "payment_url": "https://storage.example.com/payments/PAY-2024-006A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-006A",
            "payment_date": "2024-12-03",
            "amount_paid": 8400.00,
            "payment_mode": "SWIFT",
            "bank_reference": "SWIFT2024-FN-0018",
            "invoice_number": "INV-2024-006",
            "customer_id": "fintechnexus",
            "currency": "USD",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 1,
            "note": "Compute line item paid; balance held over FX rate dispute (RBI ref rate vs applied rate)",
        },
    },
    # INV-2024-006 — FinTech Nexus: balance pending (FX dispute)
    {
        "customer_id": "fintechnexus",
        "invoice_number": "INV-2024-006",
        "payment_url": "https://storage.example.com/payments/PAY-2024-006B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-006B",
            "payment_date": None,
            "amount_paid": 6200.00,
            "payment_mode": "SWIFT",
            "bank_reference": None,
            "invoice_number": "INV-2024-006",
            "customer_id": "fintechnexus",
            "currency": "USD",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance pending — INR/USD conversion rate disputed. Customer citing MSA Clause 7.3",
        },
    },
    # INV-2024-007 — MedPlus: advance payment
    {
        "customer_id": "medplus",
        "invoice_number": "INV-2024-007",
        "payment_url": "https://storage.example.com/payments/PAY-2024-007A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-007A",
            "payment_date": "2024-11-28",
            "amount_paid": 200000.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS24333002211",
            "invoice_number": "INV-2024-007",
            "customer_id": "medplus",
            "status": "CLEARED",
            "payment_type": "ADVANCE",
            "payment_sequence": 1,
            "note": "Advance against order confirmation",
        },
    },
    # INV-2024-007 — MedPlus: chargeback on failed batch
    {
        "customer_id": "medplus",
        "invoice_number": "INV-2024-007",
        "payment_url": "https://storage.example.com/payments/PAY-2024-007B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-007B",
            "payment_date": "2024-12-18",
            "amount_paid": 30000.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT24352006788",
            "invoice_number": "INV-2024-007",
            "customer_id": "medplus",
            "status": "REVERSED",
            "payment_type": "CHARGEBACK",
            "payment_sequence": 2,
            "note": "Chargeback — BATCH-API-2024-112 failed QC (assay 98.1%, spec 99.0-101.0%). Partial reversal INR 30,000",
        },
    },
    # INV-2024-008 — BuildRight: 50% advance
    {
        "customer_id": "buildright",
        "invoice_number": "INV-2024-008",
        "payment_url": "https://storage.example.com/payments/PAY-2024-008A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-008A",
            "payment_date": "2024-12-07",
            "amount_paid": 2934875.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS24342009981",
            "invoice_number": "INV-2024-008",
            "customer_id": "buildright",
            "status": "CLEARED",
            "payment_type": "ADVANCE",
            "payment_sequence": 1,
            "note": "50% advance per contract",
        },
    },
    # INV-2024-008 — BuildRight: balance pending (steel shortfall dispute)
    {
        "customer_id": "buildright",
        "invoice_number": "INV-2024-008",
        "payment_url": "https://storage.example.com/payments/PAY-2024-008B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-008B",
            "payment_date": None,
            "amount_paid": 2934875.00,
            "payment_mode": "RTGS",
            "bank_reference": None,
            "invoice_number": "INV-2024-008",
            "customer_id": "buildright",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance pending — steel delivery shortfall (46.2 MT received vs 50 MT invoiced). Deducting INR 2,46,620.",
        },
    },
    # INV-2024-009 — GreenPower: M1 advance (30%)
    {
        "customer_id": "greenpower",
        "invoice_number": "INV-2024-009",
        "payment_url": "https://storage.example.com/payments/PAY-2024-009A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-009A",
            "payment_date": "2024-12-12",
            "amount_paid": 1282155.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS24347008834",
            "invoice_number": "INV-2024-009",
            "customer_id": "greenpower",
            "status": "CLEARED",
            "payment_type": "ADVANCE",
            "payment_sequence": 1,
            "note": "M1 — 30% advance on order confirmation",
        },
    },
    # INV-2024-009 — GreenPower: M2 delivery (40%)
    {
        "customer_id": "greenpower",
        "invoice_number": "INV-2024-009",
        "payment_url": "https://storage.example.com/payments/PAY-2024-009B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-009B",
            "payment_date": "2024-12-28",
            "amount_paid": 1709540.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS24363012201",
            "invoice_number": "INV-2024-009",
            "customer_id": "greenpower",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "M2 — 40% on equipment delivery to Rajasthan site",
        },
    },
    # INV-2024-009 — GreenPower: M3 commissioning (30%) — pending DISCOM clearance
    {
        "customer_id": "greenpower",
        "invoice_number": "INV-2024-009",
        "payment_url": "https://storage.example.com/payments/PAY-2024-009C.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-009C",
            "payment_date": None,
            "amount_paid": 1282155.00,
            "payment_mode": "RTGS",
            "bank_reference": None,
            "invoice_number": "INV-2024-009",
            "customer_id": "greenpower",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 3,
            "note": "M3 — 30% final on commissioning. Delayed due to DISCOM grid sync approval (JVVNL-SYNC-2025-0144)",
        },
    },
    # INV-2024-010 — Grand Meridian: full payment
    {
        "customer_id": "grandmeridian",
        "invoice_number": "INV-2024-010",
        "payment_url": "https://storage.example.com/payments/PAY-2024-010A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-010A",
            "payment_date": "2025-01-10",
            "amount_paid": 3132900.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT25010014400",
            "invoice_number": "INV-2024-010",
            "customer_id": "grandmeridian",
            "status": "CLEARED",
            "payment_type": "FULL",
            "payment_sequence": 1,
        },
    },

    # INV-2024-011 — Acme: full payment cleared
    {
        "customer_id": "acme",
        "invoice_number": "INV-2024-011",
        "payment_url": "https://storage.example.com/payments/PAY-2024-011A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-011A",
            "payment_date": "2026-02-12",
            "amount_paid": 454300.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT26043002881",
            "invoice_number": "INV-2024-011",
            "customer_id": "acme",
            "status": "CLEARED",
            "payment_type": "FULL",
            "payment_sequence": 1,
        },
    },

    # INV-2024-012 — Acme: partial paid (withheld disputed GST INR 1,62,000)
    {
        "customer_id": "acme",
        "invoice_number": "INV-2024-012",
        "payment_url": "https://storage.example.com/payments/PAY-2024-012A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-012A",
            "payment_date": "2026-03-05",
            "amount_paid": 959000.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS26064007741",
            "invoice_number": "INV-2024-012",
            "customer_id": "acme",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 1,
            "note": "Partial payment — INR 1,62,000 withheld pending GST exemption resolution (cert GSTIN-EX-MH-2024-00881)",
        },
    },
    {
        "customer_id": "acme",
        "invoice_number": "INV-2024-012",
        "payment_url": "https://storage.example.com/payments/PAY-2024-012B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-012B",
            "payment_date": None,
            "amount_paid": 162000.00,
            "payment_mode": "RTGS",
            "bank_reference": None,
            "invoice_number": "INV-2024-012",
            "customer_id": "acme",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "GST balance held — to be paid on issuance of revised exempt invoice or credit note.",
        },
    },

    # INV-2024-013 — Acme: no payment (invoice on hold — wrong address)
    {
        "customer_id": "acme",
        "invoice_number": "INV-2024-013",
        "payment_url": "https://storage.example.com/payments/PAY-2024-013A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-013A",
            "payment_date": None,
            "amount_paid": 690300.00,
            "payment_mode": "RTGS",
            "bank_reference": None,
            "invoice_number": "INV-2024-013",
            "customer_id": "acme",
            "status": "ON_HOLD",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Payment blocked — wrong billing address on invoice (Andheri East vs Powai per GSTIN). ITC blocked until reissued.",
        },
    },
]

# =============================================================================
# EMAIL CONTENT  (14 diverse emails — ideal for AI dispute bot clarification)
# =============================================================================

EMAILS = [
    # 1. Short payment — TechSoft
    {
        "filename": "email_01_short_payment_techsoft.pdf",
        "sender": "accounts@techsoft.com",
        "subject": "Re: Invoice INV-2024-002 — Payment Clarification",
        "body": """Dear Accounts Team,

I hope this email finds you well. I am writing regarding Invoice Number INV-2024-002
dated 5th November 2024 for a total amount of INR 4,36,600.

We have processed a payment of INR 4,00,000 via RTGS (Reference: RTGS24330005678)
on 30th November 2024. However, we are disputing the remaining balance of INR 36,600.

Our Purchase Order (PO-TS-2024-089) clearly states that the Implementation Support
rate is INR 2,500 per hour, NOT INR 3,000 as billed. For 40 hours, this should be
INR 1,00,000, not INR 1,20,000.

We kindly request you to:
1. Review the agreed rate in our PO
2. Issue a credit note for INR 20,000 (the difference in implementation hours billing)
3. Adjust the tax amount accordingly

Attached is a copy of our Purchase Order for your reference.

Please confirm receipt and advise on the resolution timeline.

Best regards,
Rajesh Kumar
Finance Manager
TechSoft Solutions
accounts@techsoft.com | +91-80-4567-8901""",
    },

    # 2. Pricing mismatch — Metro Logistics
    {
        "filename": "email_02_pricing_dispute_metro.pdf",
        "sender": "finance@metro.com",
        "subject": "Dispute — Invoice INV-2024-005 Pricing Mismatch",
        "body": """To Whom It May Concern,

We are writing with respect to Invoice INV-2024-005 raised on 20th November 2024
for INR 4,77,900.

Upon reviewing this invoice against our signed contract dated 1st October 2024,
we have identified the following pricing discrepancy:

Fleet Management Software:
  - Invoiced: INR 1,80,000
  - Contract Rate: INR 1,50,000
  - Difference: INR 30,000 (OVERCHARGED)

GPS Devices (50 units):
  - Invoiced: INR 4,500 per unit = INR 2,25,000
  - Contract Rate: INR 4,000 per unit = INR 2,00,000
  - Difference: INR 25,000 (OVERCHARGED)

Total overcharge: INR 55,000 + applicable GST

We have put a hold on this payment until the matter is resolved. Please send a
revised invoice reflecting the contracted prices.

Contract reference: CONTRACT-METRO-2024-001

Regards,
Priya Sharma
CFO, Metro Logistics""",
    },

    # 3. Duplicate invoice — Acme Corp
    {
        "filename": "email_03_duplicate_invoice_acme.pdf",
        "sender": "ap@acmecorp.com",
        "subject": "Duplicate Invoice Received — INV-2024-001 and INV-2024-001-REV",
        "body": """Hi Finance Team,

We have received two invoices for the same delivery:
  - INV-2024-001 dated 1st November 2024 — INR 1,18,000
  - INV-2024-001-REV dated 8th November 2024 — INR 1,18,000

Both invoices reference PO-ACME-2024-011 and describe identical line items
(10 Office Furniture Sets, 20 Ergonomic Chairs).

We have already paid INV-2024-001 in full via NEFT on 25th November 2024
(Bank Reference: NEFT24325001234).

Please confirm which invoice is the valid one and cancel the duplicate.
Also confirm you will not be presenting INV-2024-001-REV for payment.

Best regards,
Anita Desai
Accounts Payable, Acme Corp""",
    },

    # 4. Incorrect quantity — Sunrise Retail
    {
        "filename": "email_04_incorrect_quantity_sunrise.pdf",
        "sender": "vikram.mehta@sunrise-retail.com",
        "subject": "Quantity Discrepancy — Invoice INV-2024-004",
        "body": """To the Billing Team,

We write to formally dispute the quantities on Invoice INV-2024-004 dated
15th November 2024 for INR 6,72,600.

The invoice charges for 15 Point of Sale Systems at INR 35,000 each = INR 5,25,000.
However, our Goods Received Note (GRN-SR-2024-088) confirms only 12 units were
delivered to our Andheri warehouse on 18th November 2024.

Discrepancy summary:
  - Invoiced quantity:  15 POS units
  - Delivered quantity: 12 POS units
  - Amount overcharged: 3 x INR 35,000 = INR 1,05,000 + GST INR 18,900

We have released 50% advance payment (IMPS: IMPS24324007721 — INR 3,36,300)
but are withholding the balance pending delivery of the remaining 3 units or a
credit note for the missing items.

Please contact us at the earliest.

Regards,
Vikram Mehta
Head of Accounts
Sunrise Retail Pvt Ltd
accounts@sunrise-retail.com""",
    },

    # 5. Tax error — Global Traders
    {
        "filename": "email_05_tax_error_globaltraders.pdf",
        "sender": "finance@globaltraders.com",
        "subject": "Tax Calculation Error on INV-2024-003",
        "body": """To the Finance Team,

We are writing about Invoice INV-2024-003 for INR 5,72,300 dated 10th November 2024.

While we have already made full payment (NEFT Reference: NEFT24342009012) as a
goodwill gesture to maintain our business relationship, we wish to flag a tax
calculation error for record rectification.

The invoice charges GST at 18% on the total value of INR 4,85,000.
However, as per GST guidelines:
  - Industrial Equipment (HSN 8428): 12% GST = INR 48,000
  - Spare Parts (HSN 8487): 18% GST = INR 10,800
  - Installation Service (SAC 9987): 18% GST = INR 4,500

Correct total GST: INR 63,300
Invoiced GST: INR 87,300
Excess GST charged: INR 24,000

Please issue a credit note for INR 24,000 and a revised GST invoice so we can
claim accurate input tax credit.

Warm regards,
Deepak Agarwal
Finance Controller
Global Traders Ltd""",
    },

    # 6. Payment status inquiry — Acme Corp
    {
        "filename": "email_06_payment_status_acme.pdf",
        "sender": "ap@acmecorp.com",
        "subject": "Payment Status Inquiry — INV-2024-001",
        "body": """Hi,

Following up on our payment for Invoice INV-2024-001.

We made a payment of INR 1,18,000 on 25th November 2024 via NEFT.
NEFT Reference Number: NEFT24325001234
Bank: HDFC Bank
Account debited: Acme Corp Current Account

It has been over a week and we have not received any payment confirmation or
receipt from your end. Could you please:

1. Confirm if the payment has been received and credited
2. Send us an official payment receipt / acknowledgment
3. Update the invoice status to "PAID" in your system

This is important for our month-end accounts closing.

Thank you,
Anita Desai
Accounts Payable, Acme Corp""",
    },

    # 7. Clarification — TechSoft
    {
        "filename": "email_07_clarification_techsoft.pdf",
        "sender": "accounts@techsoft.com",
        "subject": "Clarification Required — Invoice INV-2024-002 Line Items",
        "body": """Hello,

We are reviewing Invoice INV-2024-002 and require some clarifications before
we can process the remaining payment.

1. The invoice mentions "Implementation Support (40 hrs)" but we only approved
   35 hours in our work order WO-TS-2024-045. Can you please share the timesheet
   showing 40 hours were worked?

2. Is the Annual Software License price inclusive or exclusive of future upgrades?
   Our understanding from the sales discussion was that it includes major version
   upgrades for 1 year.

3. Can you clarify what support SLA is included with the license?

Once we receive these clarifications, we will process the pending balance payment
of INR 36,600 promptly.

Best regards,
Rajesh Kumar
Finance Manager, TechSoft Solutions""",
    },

    # 8. Service quality dispute — Metro Logistics
    {
        "filename": "email_08_service_quality_metro.pdf",
        "sender": "finance@metro.com",
        "subject": "Service Quality Dispute — Invoice INV-2024-005 GPS Devices",
        "body": """Dear Accounts Team,

In addition to the pricing dispute raised in our previous email regarding
Invoice INV-2024-005, we also want to formally raise a service quality concern.

The GPS devices supplied (50 units) have shown a defect rate of 20% (10 units
non-functional) within the first 2 weeks of deployment.

Issues observed:
  - 6 units: GPS signal not locking
  - 3 units: Device not powering on
  - 1 unit: Display malfunction

We have raised service tickets (Ticket IDs: GPS-001 to GPS-010) but have not
received any response in 10 days.

We are requesting:
1. Immediate replacement of 10 faulty units
2. Compensation for operational losses during this period
3. Resolution of the pricing dispute before any payment is released

Regards,
Priya Sharma
CFO, Metro Logistics""",
    },

    # 9. Currency / FX dispute — FinTech Nexus
    {
        "filename": "email_09_currency_dispute_fintechnexus.pdf",
        "sender": "treasury@fintechnexus.com",
        "subject": "Currency Conversion Dispute — Invoice INV-2024-006",
        "body": """Dear Paisa Vasool Finance Team,

We are writing regarding Invoice INV-2024-006 for USD 14,600 dated
25th November 2024.

Your billing system applied an exchange rate of USD 1 = INR 85.40 when
converting to our INR settlement account.

However, as per our Master Service Agreement (Clause 7.3), the applicable rate
is the RBI reference rate on the invoice date, which was USD 1 = INR 83.72
(source: RBI website, 25-Nov-2024).

At the correct rate:
  USD 14,600 x 83.72 = INR 12,22,312

At the rate you applied:
  USD 14,600 x 85.40 = INR 12,46,840

Difference: INR 24,528 (overcharged)

We have released payment for the Compute portion (USD 8,400 — Ref: SWIFT2024-FN-0018)
but are holding the balance of USD 6,200 pending issuance of a revised invoice
at the correct RBI reference rate.

Best regards,
Arjun Bose
Head of Treasury
FinTech Nexus Inc""",
    },

    # 10. Batch quality rejection — MedPlus Labs
    {
        "filename": "email_10_batch_quality_medplus.pdf",
        "sender": "procurement@medplus-labs.com",
        "subject": "Quality Rejection & Chargeback — INV-2024-007 Batch BATCH-API-2024-112",
        "body": """To the Quality & Finance Teams,

We are writing to formally notify you of a partial quality rejection against
Invoice INV-2024-007 dated 1st December 2024.

Our QC laboratory has completed testing of Batch BATCH-API-2024-112
(Paracetamol API, 500 kg). The batch has FAILED our Certificate of Analysis
verification on the following parameters:

  - Assay: Found 98.1% (Specification: 99.0-101.0%)
  - Related Substances Imp-A: Found 0.18% (Limit: NMT 0.15%)
  - Particle Size D90: 210 microns (Specification: NMT 180 microns)

Batch BATCH-API-2024-113 (Ibuprofen API) has passed QC — no issues there.

In line with our Vendor Quality Agreement (VQA-MPL-2024-SUPPLY), we are:
1. Rejecting and returning the full 500 kg of Paracetamol API
2. Initiating a chargeback for INR 1,60,000 + proportionate freight
3. Requesting a replacement batch with CoA from an accredited NABL lab

We have already filed chargeback PAY-2024-007B (INR 30,000 for freight) and
will file the main value reversal upon confirmation.

Please acknowledge and share your replacement timeline.

Regards,
Dr. Kavitha Rajan
Head of Procurement Quality
MedPlus Laboratories Pvt Ltd""",
    },

    # 11. Steel quantity dispute — BuildRight
    {
        "filename": "email_11_quantity_dispute_buildright.pdf",
        "sender": "finance@buildright.in",
        "subject": "Delivery Shortfall — Invoice INV-2024-008 Steel Bars",
        "body": """To the Finance & Logistics Team,

We are writing regarding Invoice INV-2024-008 for INR 58,69,750 dated 5th December 2024.

According to your invoice, 50 MT of TMT Steel Bars (Fe500D) were to be delivered.
We have received and weighed the delivery and our site engineer's measurement record
(Field Report: FR-BRI-2024-DEC-15) shows only 46.2 MT were actually received.

Shortfall: 3.8 MT x INR 55,000 = INR 2,09,000 + GST INR 37,620 = INR 2,46,620

We have paid the 50% advance (RTGS: RTGS24342009981 — INR 29,34,875) but are
revising our balance payment to deduct the shortfall amount.

Revised balance due: INR 29,34,875 - INR 2,46,620 = INR 26,88,255

We request you to:
1. Acknowledge the delivery shortfall
2. Arrange delivery of the remaining 3.8 MT OR issue a credit note
3. Confirm revised balance amount before we release payment

Warm regards,
Sameer Goyal
CFO, BuildRight Infrastructure Ltd""",
    },

    # 12. Commissioning hold — GreenPower
    {
        "filename": "email_12_commissioning_hold_greenpower.pdf",
        "sender": "accounts@greenpower.in",
        "subject": "Milestone Payment Hold — INV-2024-009 Commissioning Delay",
        "body": """Dear Finance Team,

This is to inform you that the final milestone payment of INR 12,82,155 (30%
commissioning milestone) against Invoice INV-2024-009 will be delayed.

Reason: The inverter commissioning scheduled for 15th January 2025 has been
postponed due to grid synchronisation approval pending from DISCOM
(Distribution Company Reference: JVVNL-SYNC-2025-0144).

The delay is NOT attributable to GreenPower Solutions. We are following up
with the DISCOM and expect clearance by 30th January 2025.

We want to confirm that:
1. Payments M1 and M2 (totalling INR 29,91,695) have been made in full
2. We are committed to the M3 payment of INR 12,82,155 upon commissioning
3. No interest or penalty should accrue on account of DISCOM-caused delay

Please acknowledge and confirm no late payment charges will be levied.

Best regards,
Nishant Malhotra
Director Finance
GreenPower Solutions Pvt Ltd""",
    },

    # 13. Refund request — Grand Meridian
    {
        "filename": "email_13_refund_request_grandmeridian.pdf",
        "sender": "procurement@grandmeridian.com",
        "subject": "Refund Request — INV-2024-010 Damaged Linen Sets",
        "body": """Dear Vendor Relations Team,

We are writing concerning Invoice INV-2024-010 for which full payment of
INR 31,32,900 was made on 10th January 2025 (NEFT: NEFT25010014400).

Upon detailed inspection at our Central Warehouse, our quality team identified:

1. Egyptian Cotton Bed Linen Sets (Invoiced: 500 pcs)
   - 42 sets with weaving defects (photographic evidence — Annexure A)
   - 18 sets with incorrect thread count labels (400TC labelled, found 280TC
     per lab test Report LR-GMH-2025-007)

2. Bathroom Amenities Kits (Invoiced: 2000 kits)
   - 95 kits with broken or missing pump dispensers
   - 30 kits with leaking shampoo bottles (damaged packaging)

Total items affected: 185 pieces across two categories

We request:
- Replacement of all 185 defective items within 15 days, OR
- Credit/refund of INR 3,02,400 (pro-rated value including GST)

This is time-sensitive — we have a 450-room hotel opening on 1st February 2025.

Thank you,
Rohini Kapoor
VP Procurement
Grand Meridian Hotels & Resorts""",
    },

    # 14. Milestone billing clarification — GreenPower
    {
        "filename": "email_14_milestone_clarification_greenpower.pdf",
        "sender": "accounts@greenpower.in",
        "subject": "Clarification on Milestone Billing Structure — INV-2024-009",
        "body": """Hello,

We need a few clarifications before we can process the final milestone payment
for Invoice INV-2024-009.

1. MILESTONE DEFINITION AMBIGUITY
   The contract (PO-GPS-2024-SOLAR-009) defines Milestone M3 as
   "Commissioning and Grid Synchronisation". However the invoice footnote
   says "System On". Are these treated as the same event for payment purposes?

2. PERFORMANCE GUARANTEE
   We expected a performance test report (98%+ Plant Availability Factor over
   72 hours) prior to releasing M3 payment. Has this been scheduled?

3. AS-BUILT DRAWINGS
   Our EPC team has not received the final As-Built drawings. Per Clause 9.1,
   M3 payment is contingent on receipt of As-Built drawings and O&M manuals.

4. WARRANTY COMMENCEMENT
   Please confirm whether the 5-year inverter warranty starts from commissioning
   date or from invoice date.

Once these points are clarified, we will release the INR 12,82,155 balance
without delay.

Thanks,
Nishant Malhotra
Director Finance
GreenPower Solutions Pvt Ltd""",
    },

    # 15. Follow-up payment status — Acme Corp (tests auto-dispute-link feature)
    #     This email references DISP token from the original payment inquiry (email_06).
    #     Should be auto-linked to dispute #8 via token match, NOT fork a new dispute.
    {
        "filename": "email_15_followup_payment_acme.pdf",
        "sender": "ap@acmecorp.com",
        "subject": "RE: Payment Status Inquiry — INV-2024-001 [DISP-00008]",
        "body": """Hi,

This is a follow-up to our earlier email regarding the payment status for
Invoice INV-2024-001 (Reference: DISP-00008).

We have not yet received the official payment receipt we requested.

As a reminder:
  - Payment of INR 1,18,000 was made on 25th November 2024 via NEFT
  - NEFT Reference: NEFT24325001234
  - Bank: HDFC Bank, Acme Corp Current Account

Could you please:
1. Confirm the payment has been received and credited to our account
2. Send us the official payment receipt / acknowledgment letter
3. Confirm the invoice status has been updated to \"PAID\" in your system

This is now overdue for our audit trail — we need this resolved by end of week.

Regards,
Anita Desai
Accounts Payable, Acme Corp
ap@acmecorp.com | +91-22-6789-0123""",
    },
]

# =============================================================================
# PDF Generator
# =============================================================================

def generate_email_pdf(email_data: dict, output_path: Path):
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        rightMargin=inch,
        leftMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
    )

    story = []

    header_data = [
        ["FROM:",    email_data["sender"]],
        ["TO:",      "disputes@paisavasool.com"],
        ["SUBJECT:", email_data["subject"]],
        ["DATE:",    datetime.now().strftime("%d %B %Y, %H:%M IST")],
    ]
    header_table = Table(header_data, colWidths=[1.2*inch, 5*inch])
    header_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("TEXTCOLOR",   (0, 0), (0, -1), colors.HexColor("#333333")),
        ("FONTNAME",    (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",    (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("PADDING",     (0, 0), (-1, -1), 6),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.3*inch))

    divider = Table([[""]], colWidths=[6.5*inch])
    divider.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#333333"))]))
    story.append(divider)
    story.append(Spacer(1, 0.2*inch))

    body_style = ParagraphStyle(
        "body",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        spaceAfter=8,
    )
    for line in email_data["body"].split("\n"):
        if line.strip():
            story.append(Paragraph(line.strip(), body_style))
        else:
            story.append(Spacer(1, 0.1*inch))

    doc.build(story)
    print(f"  created: {output_path.name}")


# =============================================================================
# DB Seeder
# =============================================================================

async def seed_database():
    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        print("\nSeeding dispute types...")
        for dt in DISPUTE_TYPES:
            result = await session.execute(
                text("SELECT dispute_type_id FROM dispute_type WHERE reason_name = :name"),
                {"name": dt["reason_name"]},
            )
            if result.fetchone():
                print(f"  DisputeType '{dt['reason_name']}' already exists, skipping.")
                continue

            await session.execute(
                text("""
                    INSERT INTO dispute_type (reason_name, description, severity_level, is_active)
                    VALUES (:reason_name, :description, :severity_level, true)
                """),
                {
                    "reason_name":    dt["reason_name"],
                    "description":    dt["description"],
                    "severity_level": dt["severity_level"],
                },
            )
            print(f"  DisputeType '{dt['reason_name']}' [{dt['severity_level']}] inserted")

        await session.commit()
        print(f"  {len(DISPUTE_TYPES)} default dispute types seeded (AI will add more dynamically)")

        print("\nSeeding invoices...")
        for inv in INVOICES:
            result = await session.execute(
                text("SELECT invoice_id FROM invoice_data WHERE invoice_number = :num"),
                {"num": inv["invoice_number"]},
            )
            if result.fetchone():
                print(f"  Invoice {inv['invoice_number']} already exists, skipping.")
                continue

            await session.execute(
                text("""
                    INSERT INTO invoice_data (invoice_number, invoice_url, invoice_details, updated_at)
                    VALUES (:invoice_number, :invoice_url, cast(:invoice_details as jsonb), NOW())
                """),
                {
                    "invoice_number":  inv["invoice_number"],
                    "invoice_url":     inv["invoice_url"],
                    "invoice_details": json.dumps(inv["invoice_details"]),
                },
            )
            print(f"  Invoice {inv['invoice_number']} inserted")

        print("\nSeeding payment details (multiple per invoice supported)...")
        for pay in PAYMENTS:
            result = await session.execute(
                text("SELECT payment_detail_id FROM payment_detail WHERE payment_url = :url"),
                {"url": pay["payment_url"]},
            )
            if result.fetchone():
                ref = pay["payment_details"].get("payment_reference", pay["payment_url"])
                print(f"  Payment {ref} already exists, skipping.")
                continue

            await session.execute(
                text("""
                    INSERT INTO payment_detail (customer_id, invoice_number, payment_url, payment_details)
                    VALUES (:customer_id, :invoice_number, :payment_url, cast(:payment_details as jsonb))
                """),
                {
                    "customer_id":    pay["customer_id"],
                    "invoice_number": pay["invoice_number"],
                    "payment_url":    pay["payment_url"],
                    "payment_details": json.dumps(pay["payment_details"]),
                },
            )
            ref  = pay["payment_details"].get("payment_reference", "N/A")
            ptype = pay["payment_details"].get("payment_type", "?")
            print(f"  Payment {ref} [{ptype}] for {pay['invoice_number']} inserted")

        await session.commit()

    await engine.dispose()
    print("\nDatabase seeding complete!")


# =============================================================================
# Main
# =============================================================================

async def main():
    print("=" * 62)
    print("  Paisa Vasool — Diverse Sample Data Generator")
    print("=" * 62)

    print(f"\nGenerating {len(EMAILS)} email PDFs into ./{OUTPUT_DIR}/")
    for email_data in EMAILS:
        generate_email_pdf(email_data, OUTPUT_DIR / email_data["filename"])

    print(f"\nConnecting to DB: {DATABASE_URL.split('@')[-1]}")
    await seed_database()

    print("\n" + "=" * 62)
    print("  Done! Quick start:")
    print("=" * 62)
    print(f"\n  Email PDFs: ./{OUTPUT_DIR}/")
    print("\n  Fetch all payments for a multi-milestone invoice:")
    print("  GET /api/v1/payments/by-invoice/INV-2024-009   (3 payments)")
    print("  GET /api/v1/payments/by-invoice/INV-2024-002   (2 payments)")
    print("\n  View supporting docs for a dispute:")
    print("  GET /api/v1/disputes/{dispute_id}/supporting-docs")
    print("\n  Add a supporting doc to a dispute:")
    print("  POST /api/v1/disputes/{dispute_id}/supporting-docs")
    print("""  Body: {
    "analysis_id": 1,
    "reference_table": "payment_detail",
    "ref_id_value": 3,
    "context_note": "PAY-2024-003A shows full payment was made before dispute was raised"
  }""")


if __name__ == "__main__":
    asyncio.run(main())