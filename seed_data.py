"""
seed_data.py
============
Run this script to:
  1. Generate 16 realistic customer email PDFs (saved to ./sample_emails/)
  2. Seed the database with matching InvoiceData and PaymentDetail records

Customers:
  baranekumar56@gmail.com       — individual/freelancer (gmail, generic domain)
  717822p107@kce.ac.in          — college/institution (corporate domain kce.ac.in)
  jeevadharani9384@gmail.com    — individual (gmail, generic domain)
  prakeshprakesh9345@gmail.com  — individual (gmail, generic domain)

Cases covered:
  ✓ Short payment / pricing mismatch
  ✓ Duplicate invoice
  ✓ Goods not received / quantity dispute
  ✓ Tax / GST dispute
  ✓ Payment not reflected
  ✓ Quality dispute (damaged goods)
  ✓ Early payment discount claim
  ✓ Payment terms dispute
  ✓ General clarification (no invoice — triggers clarification flow)
  ✓ Ownership unverified (wrong sender for invoice — triggers UNVERIFIED)
  ✓ Multi-issue single email (triggers inline dispute creation)
  ✓ Context shift / forked dispute
  ✓ Follow-up reply threading
  ✓ Milestone / partial payment dispute

Usage:
    python seed_data.py

Requirements:
    pip install reportlab asyncpg sqlalchemy asyncio
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors

# ── PDF generation ────────────────────────────────────────────────────────────
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import text

# ── DB ────────────────────────────────────────────────────────────────────────
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/auth_db",
)

OUTPUT_DIR = Path("sample_emails")
OUTPUT_DIR.mkdir(exist_ok=True)

styles = getSampleStyleSheet()

# =============================================================================
# DISPUTE TYPES
# =============================================================================

DISPUTE_TYPES = [
    {
        "reason_name": "Pricing Mismatch",
        "description": "Customer claims the price charged differs from the agreed or quoted price.",
        "severity_level": "HIGH",
    },
    {
        "reason_name": "Short Payment",
        "description": "Customer has paid less than the invoiced amount without prior agreement.",
        "severity_level": "HIGH",
    },
    {
        "reason_name": "Duplicate Invoice",
        "description": "Customer believes they have been billed twice for the same goods or services.",
        "severity_level": "HIGH",
    },
    {
        "reason_name": "Tax Dispute",
        "description": "Customer disputes the tax rate, tax amount, or tax exemption applied on the invoice.",
        "severity_level": "MEDIUM",
    },
    {
        "reason_name": "Payment Not Reflected",
        "description": "Customer claims payment was made but it has not been applied to the invoice on record.",
        "severity_level": "HIGH",
    },
    {
        "reason_name": "Goods Not Received",
        "description": "Customer disputes the invoice because the goods or services were not delivered.",
        "severity_level": "HIGH",
    },
    {
        "reason_name": "Quality Dispute",
        "description": "Customer received goods or services but disputes the quality or completeness of delivery.",
        "severity_level": "MEDIUM",
    },
    {
        "reason_name": "Early Payment Discount",
        "description": "Customer claims an early payment discount was applicable but was not reflected on the invoice.",
        "severity_level": "MEDIUM",
    },
    {
        "reason_name": "General Clarification",
        "description": "General inquiries and clarification requests that do not constitute a formal dispute.",
        "severity_level": "LOW",
    },
    {
        "reason_name": "Payment Terms Dispute",
        "description": "Customer disputes the payment due date, credit period, or agreed payment terms on the invoice.",
        "severity_level": "MEDIUM",
    },
]

# =============================================================================
# INVOICES
# Customer IDs map to real sender emails for ownership verification:
#   baranekumar56@gmail.com       → customer_id on payment record
#   717822p107@kce.ac.in          → customer_id on payment record
#   jeevadharani9384@gmail.com    → customer_id on payment record
#   prakeshprakesh9345@gmail.com  → customer_id on payment record
# =============================================================================

INVOICES = [
    # ── baranekumar56@gmail.com ───────────────────────────────────────────────
    # INV-2025-001 — Laptop + accessories (pricing mismatch dispute)
    {
        "invoice_number": "INV-2025-001",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-001.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-001",
            "invoice_date": "2025-01-10",
            "due_date": "2025-02-09",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Baran Kumar",
            "line_items": [
                {
                    "description": "Laptop Dell Inspiron 15 3520",
                    "qty": 1,
                    "unit_price": 62000.00,
                    "total": 62000.00,
                },
                {
                    "description": "Wireless Mouse & Keyboard Combo",
                    "qty": 1,
                    "unit_price": 3500.00,
                    "total": 3500.00,
                },
                {
                    "description": "Laptop Bag Premium",
                    "qty": 1,
                    "unit_price": 2500.00,
                    "total": 2500.00,
                },
            ],
            "subtotal": 68000.00,
            "tax_rate_pct": 18,
            "tax_amount": 12240.00,
            "total_amount": 80240.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-BK-2025-001",
            "notes": "Quoted price for laptop was INR 58,000 per email dated 5-Jan-2025. Invoice shows INR 62,000.",
        },
    },
    # INV-2025-002 — Office stationery (duplicate invoice)
    {
        "invoice_number": "INV-2025-002",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-002.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-002",
            "invoice_date": "2025-01-20",
            "due_date": "2025-02-19",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Baran Kumar",
            "line_items": [
                {
                    "description": "A4 Paper Ream (500 sheets)",
                    "qty": 20,
                    "unit_price": 350.00,
                    "total": 7000.00,
                },
                {
                    "description": "Pen Box (Blue Ink, 10 pcs)",
                    "qty": 5,
                    "unit_price": 120.00,
                    "total": 600.00,
                },
                {
                    "description": "Stapler Heavy Duty",
                    "qty": 2,
                    "unit_price": 450.00,
                    "total": 900.00,
                },
            ],
            "subtotal": 8500.00,
            "tax_rate_pct": 12,
            "tax_amount": 1020.00,
            "total_amount": 9520.00,
            "currency": "INR",
            "payment_terms": "Net 15",
            "po_reference": "PO-BK-2025-002",
        },
    },
    # INV-2025-003 — Furniture (payment not reflected)
    {
        "invoice_number": "INV-2025-003",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-003.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-003",
            "invoice_date": "2025-02-01",
            "due_date": "2025-03-03",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Baran Kumar",
            "line_items": [
                {
                    "description": "Executive Office Desk (L-shaped)",
                    "qty": 1,
                    "unit_price": 18000.00,
                    "total": 18000.00,
                },
                {
                    "description": "Ergonomic Office Chair",
                    "qty": 1,
                    "unit_price": 12000.00,
                    "total": 12000.00,
                },
                {
                    "description": "3-Door Steel Almirah",
                    "qty": 1,
                    "unit_price": 9500.00,
                    "total": 9500.00,
                },
            ],
            "subtotal": 39500.00,
            "tax_rate_pct": 18,
            "tax_amount": 7110.00,
            "total_amount": 46610.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-BK-2025-003",
        },
    },
    # ── 717822p107@kce.ac.in ──────────────────────────────────────────────────
    # INV-2025-004 — Lab equipment for college (goods not received)
    {
        "invoice_number": "INV-2025-004",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-004.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-004",
            "invoice_date": "2025-01-15",
            "due_date": "2025-02-14",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "KCE Engineering College",
            "line_items": [
                {
                    "description": "Arduino Uno Starter Kit",
                    "qty": 30,
                    "unit_price": 1200.00,
                    "total": 36000.00,
                },
                {
                    "description": "Raspberry Pi 4 Model B 4GB",
                    "qty": 10,
                    "unit_price": 5500.00,
                    "total": 55000.00,
                },
                {
                    "description": "Digital Multimeter",
                    "qty": 20,
                    "unit_price": 800.00,
                    "total": 16000.00,
                },
                {
                    "description": "Breadboard + Jumper Wire Set",
                    "qty": 50,
                    "unit_price": 150.00,
                    "total": 7500.00,
                },
            ],
            "subtotal": 114500.00,
            "tax_rate_pct": 18,
            "tax_amount": 20610.00,
            "total_amount": 135110.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-KCE-2025-LAB-001",
            "delivery_note": "Partial delivery received — Raspberry Pi units not delivered as of invoice date.",
        },
    },
    # INV-2025-005 — Annual software subscription (tax dispute)
    {
        "invoice_number": "INV-2025-005",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-005.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-005",
            "invoice_date": "2025-02-01",
            "due_date": "2025-03-03",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "KCE Engineering College",
            "line_items": [
                {
                    "description": "MATLAB Campus License Annual",
                    "qty": 1,
                    "unit_price": 120000.00,
                    "total": 120000.00,
                },
                {
                    "description": "AutoCAD Education License (seats)",
                    "qty": 50,
                    "unit_price": 800.00,
                    "total": 40000.00,
                },
            ],
            "subtotal": 160000.00,
            "tax_amount_charged": 28800.00,
            "tax_rate_pct": 18,
            "total_amount": 188800.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-KCE-2025-SW-001",
            "notes": "Educational institutions qualify for GST exemption on software licenses under Notification 12/2017. Tax should be 0%.",
        },
    },
    # INV-2025-006 — Canteen supplies (quality dispute + context shift — multi invoice)
    {
        "invoice_number": "INV-2025-006",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-006.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-006",
            "invoice_date": "2025-02-10",
            "due_date": "2025-03-12",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "KCE Engineering College",
            "line_items": [
                {
                    "description": "Rice (25kg bag)",
                    "qty": 40,
                    "unit_price": 1800.00,
                    "total": 72000.00,
                },
                {
                    "description": "Cooking Oil (15L can)",
                    "qty": 20,
                    "unit_price": 2200.00,
                    "total": 44000.00,
                },
                {
                    "description": "Disposable Plates (pack of 100)",
                    "qty": 50,
                    "unit_price": 250.00,
                    "total": 12500.00,
                },
            ],
            "subtotal": 128500.00,
            "tax_rate_pct": 5,
            "tax_amount": 6425.00,
            "total_amount": 134925.00,
            "currency": "INR",
            "payment_terms": "Net 15",
            "po_reference": "PO-KCE-2025-CAN-001",
        },
    },
    # ── jeevadharani9384@gmail.com ────────────────────────────────────────────
    # INV-2025-007 — Garments order (early payment discount not applied)
    {
        "invoice_number": "INV-2025-007",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-007.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-007",
            "invoice_date": "2025-01-05",
            "due_date": "2025-02-04",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Jeeva Dharani",
            "line_items": [
                {
                    "description": "Cotton Sarees (assorted, pcs)",
                    "qty": 50,
                    "unit_price": 1200.00,
                    "total": 60000.00,
                },
                {
                    "description": "Dress Material Sets",
                    "qty": 30,
                    "unit_price": 800.00,
                    "total": 24000.00,
                },
                {
                    "description": "Embroidered Blouse Pieces",
                    "qty": 40,
                    "unit_price": 450.00,
                    "total": 18000.00,
                },
            ],
            "subtotal": 102000.00,
            "tax_rate_pct": 5,
            "tax_amount": 5100.00,
            "total_amount": 107100.00,
            "currency": "INR",
            "payment_terms": "2/10 Net 30 (2% discount if paid within 10 days)",
            "po_reference": "PO-JD-2025-001",
            "discount_note": "2% early payment discount = INR 2,142 applicable if paid by 15-Jan-2025.",
        },
    },
    # INV-2025-008 — Electronics (damaged goods on delivery — quality dispute)
    {
        "invoice_number": "INV-2025-008",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-008.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-008",
            "invoice_date": "2025-01-18",
            "due_date": "2025-02-17",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Jeeva Dharani",
            "line_items": [
                {
                    "description": "Mixer Grinder 750W",
                    "qty": 5,
                    "unit_price": 3200.00,
                    "total": 16000.00,
                },
                {
                    "description": "Electric Kettle 1.5L",
                    "qty": 8,
                    "unit_price": 1100.00,
                    "total": 8800.00,
                },
                {
                    "description": "Induction Cooktop 2000W",
                    "qty": 3,
                    "unit_price": 2800.00,
                    "total": 8400.00,
                },
            ],
            "subtotal": 33200.00,
            "tax_rate_pct": 18,
            "tax_amount": 5976.00,
            "total_amount": 39176.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-JD-2025-002",
            "notes": "2 mixer grinders arrived with cracked lids. 1 induction cooktop with shattered glass top.",
        },
    },
    # INV-2025-009 — Tailoring services (payment terms dispute)
    {
        "invoice_number": "INV-2025-009",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-009.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-009",
            "invoice_date": "2025-02-15",
            "due_date": "2025-02-22",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Jeeva Dharani",
            "line_items": [
                {
                    "description": "Custom Embroidery Work (pieces)",
                    "qty": 100,
                    "unit_price": 350.00,
                    "total": 35000.00,
                },
                {
                    "description": "Stitching Services — Blouses",
                    "qty": 50,
                    "unit_price": 200.00,
                    "total": 10000.00,
                },
            ],
            "subtotal": 45000.00,
            "tax_rate_pct": 5,
            "tax_amount": 2250.00,
            "total_amount": 47250.00,
            "currency": "INR",
            "payment_terms": "Net 7",
            "agreed_payment_terms": "Net 30",
            "po_reference": "PO-JD-2025-003",
            "notes": "Agreement email dated 10-Feb-2025 clearly states Net 30. Invoice shows Net 7 — due date wrong.",
        },
    },
    # ── prakeshprakesh9345@gmail.com ──────────────────────────────────────────
    # INV-2025-010 — Printing services (short payment scenario)
    {
        "invoice_number": "INV-2025-010",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-010.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-010",
            "invoice_date": "2025-01-08",
            "due_date": "2025-02-07",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Prakesh P",
            "line_items": [
                {
                    "description": "Flex Banner Printing (sqft)",
                    "qty": 200,
                    "unit_price": 45.00,
                    "total": 9000.00,
                },
                {
                    "description": "Brochure Printing A4 (colour)",
                    "qty": 1000,
                    "unit_price": 8.00,
                    "total": 8000.00,
                },
                {
                    "description": "Visiting Cards (box of 100)",
                    "qty": 10,
                    "unit_price": 350.00,
                    "total": 3500.00,
                },
                {
                    "description": "Lamination — Matte (sheets)",
                    "qty": 500,
                    "unit_price": 4.00,
                    "total": 2000.00,
                },
            ],
            "subtotal": 22500.00,
            "tax_rate_pct": 12,
            "tax_amount": 2700.00,
            "total_amount": 25200.00,
            "currency": "INR",
            "payment_terms": "Net 15",
            "po_reference": "PO-PP-2025-001",
        },
    },
    # INV-2025-011 — Event management (multi-issue: pricing + goods not received)
    {
        "invoice_number": "INV-2025-011",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-011.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-011",
            "invoice_date": "2025-01-25",
            "due_date": "2025-02-24",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Prakesh P",
            "line_items": [
                {
                    "description": "Stage Setup & Decoration",
                    "qty": 1,
                    "unit_price": 35000.00,
                    "total": 35000.00,
                },
                {
                    "description": "PA System Rental (2 days)",
                    "qty": 2,
                    "unit_price": 8000.00,
                    "total": 16000.00,
                },
                {
                    "description": "Photography (event, 8hrs)",
                    "qty": 1,
                    "unit_price": 12000.00,
                    "total": 12000.00,
                },
                {
                    "description": "Catering — Veg Buffet (per head)",
                    "qty": 200,
                    "unit_price": 450.00,
                    "total": 90000.00,
                },
                {
                    "description": "LED Screen 12x8ft (day)",
                    "qty": 1,
                    "unit_price": 15000.00,
                    "total": 15000.00,
                },
            ],
            "subtotal": 168000.00,
            "tax_rate_pct": 18,
            "tax_amount": 30240.00,
            "total_amount": 198240.00,
            "currency": "INR",
            "payment_terms": "50% advance, balance on event day",
            "po_reference": "PO-PP-2025-002",
            "notes": "LED Screen was not delivered. Catering count was 150 pax not 200. Two disputes in same email.",
        },
    },
    # INV-2025-012 — Courier services (payment terms dispute + context shift email)
    {
        "invoice_number": "INV-2025-012",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-012.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-012",
            "invoice_date": "2025-02-05",
            "due_date": "2025-02-12",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "Prakesh P",
            "line_items": [
                {
                    "description": "Express Courier — Domestic (shipments)",
                    "qty": 50,
                    "unit_price": 180.00,
                    "total": 9000.00,
                },
                {
                    "description": "Bulk Parcel — 5kg slab (shipments)",
                    "qty": 30,
                    "unit_price": 320.00,
                    "total": 9600.00,
                },
                {
                    "description": "COD Handling Charges",
                    "qty": 20,
                    "unit_price": 50.00,
                    "total": 1000.00,
                },
            ],
            "subtotal": 19600.00,
            "tax_rate_pct": 18,
            "tax_amount": 3528.00,
            "total_amount": 23128.00,
            "currency": "INR",
            "payment_terms": "Net 7",
            "agreed_payment_terms": "Net 30",
            "po_reference": "PO-PP-2025-003",
        },
    },
    # ── UNVERIFIED ownership test case ────────────────────────────────────────
    # INV-2025-013 — belongs to 717822p107@kce.ac.in
    # Email sent by prakeshprakesh9345@gmail.com — should trigger UNVERIFIED
    {
        "invoice_number": "INV-2025-013",
        "invoice_url": "https://storage.example.com/invoices/INV-2025-013.pdf",
        "invoice_details": {
            "invoice_number": "INV-2025-013",
            "invoice_date": "2025-02-20",
            "due_date": "2025-03-22",
            "vendor_name": "Paisa Vasool Supplies Pvt Ltd",
            "customer_name": "KCE Engineering College",
            "line_items": [
                {
                    "description": "Projector Epson EB-X41",
                    "qty": 5,
                    "unit_price": 28000.00,
                    "total": 140000.00,
                },
                {
                    "description": "Projection Screen 120-inch",
                    "qty": 5,
                    "unit_price": 4500.00,
                    "total": 22500.00,
                },
                {
                    "description": "HDMI Cables (2m)",
                    "qty": 20,
                    "unit_price": 350.00,
                    "total": 7000.00,
                },
            ],
            "subtotal": 169500.00,
            "tax_rate_pct": 18,
            "tax_amount": 30510.00,
            "total_amount": 200010.00,
            "currency": "INR",
            "payment_terms": "Net 30",
            "po_reference": "PO-KCE-2025-AV-001",
        },
    },
]

# =============================================================================
# PAYMENTS
# customer_id MUST match sender email for ownership verification
# =============================================================================

PAYMENTS = [
    # ── baranekumar56@gmail.com ───────────────────────────────────────────────
    # INV-2025-001 — partial paid (pricing dispute, balance withheld)
    {
        "customer_id": "baranekumar56@gmail.com",
        "invoice_number": "INV-2025-001",
        "payment_url": "https://storage.example.com/payments/PAY-2025-001A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-001A",
            "payment_date": "2025-01-28",
            "amount_paid": 73840.00,
            "payment_mode": "UPI",
            "bank_reference": "UPI25028001234",
            "invoice_number": "INV-2025-001",
            "customer_id": "baranekumar56@gmail.com",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 1,
            "note": "Partial payment — INR 6,400 withheld. Quoted price was INR 58,000 not INR 62,000 for laptop.",
        },
    },
    {
        "customer_id": "baranekumar56@gmail.com",
        "invoice_number": "INV-2025-001",
        "payment_url": "https://storage.example.com/payments/PAY-2025-001B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-001B",
            "payment_date": None,
            "amount_paid": 6400.00,
            "payment_mode": "UPI",
            "bank_reference": None,
            "invoice_number": "INV-2025-001",
            "customer_id": "baranekumar56@gmail.com",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance held pending pricing dispute resolution.",
        },
    },
    # INV-2025-002 — full payment made (duplicate invoice dispute)
    {
        "customer_id": "baranekumar56@gmail.com",
        "invoice_number": "INV-2025-002",
        "payment_url": "https://storage.example.com/payments/PAY-2025-002A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-002A",
            "payment_date": "2025-01-30",
            "amount_paid": 9520.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT25030004411",
            "invoice_number": "INV-2025-002",
            "customer_id": "baranekumar56@gmail.com",
            "status": "CLEARED",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Paid in full. Duplicate invoice INV-2025-002-DUP received — requesting cancellation.",
        },
    },
    # INV-2025-003 — payment made but not reflected
    {
        "customer_id": "baranekumar56@gmail.com",
        "invoice_number": "INV-2025-003",
        "payment_url": "https://storage.example.com/payments/PAY-2025-003A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-003A",
            "payment_date": "2025-02-20",
            "amount_paid": 46610.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT25051007782",
            "invoice_number": "INV-2025-003",
            "customer_id": "baranekumar56@gmail.com",
            "status": "PENDING",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Payment made on 20-Feb-2025. Bank confirms debit. Invoice still shows UNPAID.",
        },
    },
    # ── 717822p107@kce.ac.in ──────────────────────────────────────────────────
    # INV-2025-004 — advance paid, balance held (goods not fully received)
    {
        "customer_id": "717822p107@kce.ac.in",
        "invoice_number": "INV-2025-004",
        "payment_url": "https://storage.example.com/payments/PAY-2025-004A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-004A",
            "payment_date": "2025-01-25",
            "amount_paid": 79610.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT25025008811",
            "invoice_number": "INV-2025-004",
            "customer_id": "717822p107@kce.ac.in",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 1,
            "note": "Partial payment. Raspberry Pi units (10 nos, INR 55,500) not delivered — withheld.",
        },
    },
    {
        "customer_id": "717822p107@kce.ac.in",
        "invoice_number": "INV-2025-004",
        "payment_url": "https://storage.example.com/payments/PAY-2025-004B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-004B",
            "payment_date": None,
            "amount_paid": 55500.00,
            "payment_mode": "NEFT",
            "bank_reference": None,
            "invoice_number": "INV-2025-004",
            "customer_id": "717822p107@kce.ac.in",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance held until Raspberry Pi units are delivered.",
        },
    },
    # INV-2025-005 — full payment made, GST tax dispute
    {
        "customer_id": "717822p107@kce.ac.in",
        "invoice_number": "INV-2025-005",
        "payment_url": "https://storage.example.com/payments/PAY-2025-005A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-005A",
            "payment_date": "2025-02-10",
            "amount_paid": 188800.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS25041006634",
            "invoice_number": "INV-2025-005",
            "customer_id": "717822p107@kce.ac.in",
            "status": "CLEARED",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Paid in full as goodwill. Requesting credit note INR 28,800 — GST exemption for education.",
        },
    },
    # INV-2025-006 — advance paid (quality dispute on canteen items)
    {
        "customer_id": "717822p107@kce.ac.in",
        "invoice_number": "INV-2025-006",
        "payment_url": "https://storage.example.com/payments/PAY-2025-006A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-006A",
            "payment_date": "2025-02-15",
            "amount_paid": 67462.50,
            "payment_mode": "UPI",
            "bank_reference": "UPI25046009920",
            "invoice_number": "INV-2025-006",
            "customer_id": "717822p107@kce.ac.in",
            "status": "CLEARED",
            "payment_type": "ADVANCE",
            "payment_sequence": 1,
            "note": "50% advance. Balance withheld — rice quality substandard, 15 bags rejected.",
        },
    },
    # INV-2025-013 — ownership check: customer_id is kce, NOT prakesh (UNVERIFIED test)
    {
        "customer_id": "717822p107@kce.ac.in",
        "invoice_number": "INV-2025-013",
        "payment_url": "https://storage.example.com/payments/PAY-2025-013A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-013A",
            "payment_date": None,
            "amount_paid": 200010.00,
            "payment_mode": "NEFT",
            "bank_reference": None,
            "invoice_number": "INV-2025-013",
            "customer_id": "717822p107@kce.ac.in",
            "status": "PENDING",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Awaiting payment.",
        },
    },
    # ── jeevadharani9384@gmail.com ────────────────────────────────────────────
    # INV-2025-007 — paid early but discount not applied
    {
        "customer_id": "jeevadharani9384@gmail.com",
        "invoice_number": "INV-2025-007",
        "payment_url": "https://storage.example.com/payments/PAY-2025-007A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-007A",
            "payment_date": "2025-01-13",
            "amount_paid": 107100.00,
            "payment_mode": "UPI",
            "bank_reference": "UPI25013002288",
            "invoice_number": "INV-2025-007",
            "customer_id": "jeevadharani9384@gmail.com",
            "status": "CLEARED",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Paid on 13-Jan-2025 (within 10-day window). 2% discount INR 2,142 not applied. Requesting credit note.",
        },
    },
    # INV-2025-008 — partial paid (damaged goods withheld)
    {
        "customer_id": "jeevadharani9384@gmail.com",
        "invoice_number": "INV-2025-008",
        "payment_url": "https://storage.example.com/payments/PAY-2025-008A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-008A",
            "payment_date": "2025-02-01",
            "amount_paid": 25376.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT25032007741",
            "invoice_number": "INV-2025-008",
            "customer_id": "jeevadharani9384@gmail.com",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 1,
            "note": "Balance INR 13,800 withheld. 2 mixer grinders + 1 induction cooktop damaged on delivery.",
        },
    },
    {
        "customer_id": "jeevadharani9384@gmail.com",
        "invoice_number": "INV-2025-008",
        "payment_url": "https://storage.example.com/payments/PAY-2025-008B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-008B",
            "payment_date": None,
            "amount_paid": 13800.00,
            "payment_mode": "NEFT",
            "bank_reference": None,
            "invoice_number": "INV-2025-008",
            "customer_id": "jeevadharani9384@gmail.com",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance pending replacement or credit note for damaged items.",
        },
    },
    # INV-2025-009 — payment withheld (payment terms dispute)
    {
        "customer_id": "jeevadharani9384@gmail.com",
        "invoice_number": "INV-2025-009",
        "payment_url": "https://storage.example.com/payments/PAY-2025-009A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-009A",
            "payment_date": None,
            "amount_paid": 47250.00,
            "payment_mode": "UPI",
            "bank_reference": None,
            "invoice_number": "INV-2025-009",
            "customer_id": "jeevadharani9384@gmail.com",
            "status": "PENDING",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Payment withheld. Due date on invoice is wrong — agreed terms were Net 30, not Net 7.",
        },
    },
    # ── prakeshprakesh9345@gmail.com ──────────────────────────────────────────
    # INV-2025-010 — partial paid (short payment)
    {
        "customer_id": "prakeshprakesh9345@gmail.com",
        "invoice_number": "INV-2025-010",
        "payment_url": "https://storage.example.com/payments/PAY-2025-010A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-010A",
            "payment_date": "2025-01-18",
            "amount_paid": 20000.00,
            "payment_mode": "UPI",
            "bank_reference": "UPI25018005531",
            "invoice_number": "INV-2025-010",
            "customer_id": "prakeshprakesh9345@gmail.com",
            "status": "CLEARED",
            "payment_type": "PARTIAL",
            "payment_sequence": 1,
            "note": "Partial payment. Disputing flex banner rate — quoted INR 38/sqft, billed INR 45/sqft.",
        },
    },
    {
        "customer_id": "prakeshprakesh9345@gmail.com",
        "invoice_number": "INV-2025-010",
        "payment_url": "https://storage.example.com/payments/PAY-2025-010B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-010B",
            "payment_date": None,
            "amount_paid": 5200.00,
            "payment_mode": "UPI",
            "bank_reference": None,
            "invoice_number": "INV-2025-010",
            "customer_id": "prakeshprakesh9345@gmail.com",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance held pending pricing dispute on flex banner rate.",
        },
    },
    # INV-2025-011 — advance paid (multi-issue: pricing + goods not received)
    {
        "customer_id": "prakeshprakesh9345@gmail.com",
        "invoice_number": "INV-2025-011",
        "payment_url": "https://storage.example.com/payments/PAY-2025-011A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-011A",
            "payment_date": "2025-01-28",
            "amount_paid": 99120.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT25028009812",
            "invoice_number": "INV-2025-011",
            "customer_id": "prakeshprakesh9345@gmail.com",
            "status": "CLEARED",
            "payment_type": "ADVANCE",
            "payment_sequence": 1,
            "note": "50% advance. LED screen not delivered. Catering 150 pax not 200. Balance disputed.",
        },
    },
    {
        "customer_id": "prakeshprakesh9345@gmail.com",
        "invoice_number": "INV-2025-011",
        "payment_url": "https://storage.example.com/payments/PAY-2025-011B.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-011B",
            "payment_date": None,
            "amount_paid": 99120.00,
            "payment_mode": "NEFT",
            "bank_reference": None,
            "invoice_number": "INV-2025-011",
            "customer_id": "prakeshprakesh9345@gmail.com",
            "status": "PENDING",
            "payment_type": "PARTIAL",
            "payment_sequence": 2,
            "note": "Balance withheld. LED screen credit + catering count correction needed.",
        },
    },
    # INV-2025-012 — payment withheld (payment terms dispute)
    {
        "customer_id": "prakeshprakesh9345@gmail.com",
        "invoice_number": "INV-2025-012",
        "payment_url": "https://storage.example.com/payments/PAY-2025-012A.pdf",
        "payment_details": {
            "payment_reference": "PAY-2025-012A",
            "payment_date": None,
            "amount_paid": 23128.00,
            "payment_mode": "UPI",
            "bank_reference": None,
            "invoice_number": "INV-2025-012",
            "customer_id": "prakeshprakesh9345@gmail.com",
            "status": "PENDING",
            "payment_type": "FULL",
            "payment_sequence": 1,
            "note": "Payment withheld. Invoice says Net 7, agreement says Net 30. Disputing due date.",
        },
    },
]

# =============================================================================
# EMAILS — 16 scenarios covering every pipeline path
# =============================================================================

EMAILS = [
    # ── baranekumar56@gmail.com ───────────────────────────────────────────────
    # 1. Pricing mismatch (INV-2025-001)
    {
        "filename": "email_01_pricing_mismatch_baran.pdf",
        "sender": "baranekumar56@gmail.com",
        "subject": "Pricing Dispute — Invoice INV-2025-001",
        "body": """Dear Finance Team,

I am writing regarding Invoice INV-2025-001 dated 10th January 2025 for INR 80,240.

The invoice charges INR 62,000 for the Dell Inspiron 15 3520 laptop. However, your
sales representative Mr. Arun confirmed the price as INR 58,000 in his email dated
5th January 2025 (reference: your quote QT-2025-BK-001).

The difference is INR 4,000 on the laptop alone, plus additional GST of INR 720.
Total overcharge: INR 4,720.

I have already made payment of INR 73,840 via UPI (Reference: UPI25028001234) on
28th January 2025. I am withholding the balance of INR 6,400 pending correction.

Please issue a revised invoice at the agreed price of INR 58,000 and refund or
adjust the overcharged amount.

Regards,
Baran Kumar
baranekumar56@gmail.com""",
    },
    # 2. Duplicate invoice (INV-2025-002)
    {
        "filename": "email_02_duplicate_invoice_baran.pdf",
        "sender": "baranekumar56@gmail.com",
        "subject": "Duplicate Invoice — Please Confirm INV-2025-002",
        "body": """Hi,

I received two invoices for the same stationery order:
  - INV-2025-002 dated 20th January 2025 — INR 9,520
  - INV-2025-002-DUP dated 22nd January 2025 — INR 9,520 (same items, same amounts)

I have already paid INV-2025-002 in full on 30th January 2025 via NEFT
(Reference: NEFT25030004411 — INR 9,520).

Please confirm which invoice is valid and issue a cancellation notice for the
duplicate so I am not billed twice. Also confirm no auto-debit will be triggered
for the second invoice.

Thank you,
Baran Kumar
baranekumar56@gmail.com""",
    },
    # 3. Payment not reflected (INV-2025-003)
    {
        "filename": "email_03_payment_not_reflected_baran.pdf",
        "sender": "baranekumar56@gmail.com",
        "subject": "Payment Made — Not Reflected on Invoice INV-2025-003",
        "body": """Hello,

I am following up on Invoice INV-2025-003 for INR 46,610 (office furniture).

I made full payment of INR 46,610 on 20th February 2025 via NEFT
(Reference: NEFT25051007782). My bank statement shows the amount was debited
and the beneficiary account was credited on the same day.

However, your portal and the invoice still show status as UNPAID. This is
causing issues for my GST input tax credit claim.

Could you please:
1. Verify receipt of NEFT-NEFT25051007782
2. Update the invoice status to PAID
3. Send me the official payment receipt

Regards,
Baran Kumar""",
    },
    # 4. No invoice — general clarification (triggers clarification flow)
    {
        "filename": "email_04_clarification_no_invoice_baran.pdf",
        "sender": "baranekumar56@gmail.com",
        "subject": "Query regarding my recent order",
        "body": """Hello Team,

I placed an order for some office supplies last week but I have not received the
invoice or any order confirmation email yet. Could you please check the status
and let me know when I can expect delivery and the invoice?

My registered email is baranekumar56@gmail.com.

Thanks,
Baran""",
    },
    # ── 717822p107@kce.ac.in ──────────────────────────────────────────────────
    # 5. Goods not received — partial delivery (INV-2025-004)
    {
        "filename": "email_05_goods_not_received_kce.pdf",
        "sender": "717822p107@kce.ac.in",
        "subject": "Partial Delivery — Invoice INV-2025-004",
        "body": """Dear Accounts Team,

I am writing regarding Invoice INV-2025-004 dated 15th January 2025 for INR 1,35,110
(lab equipment for KCE Engineering College).

We have received the following items:
  ✓ 30 Arduino Uno Starter Kits
  ✓ 20 Digital Multimeters
  ✓ 50 Breadboard + Jumper Wire Sets

NOT received:
  ✗ 10 Raspberry Pi 4 Model B 4GB units (INR 55,000 + GST INR 9,900 = INR 64,900 approx)

We have released partial payment of INR 79,610 via NEFT (PAY-2025-004A) for the
items received. We are withholding INR 55,500 until the Raspberry Pi units are
delivered or a credit note is issued.

Please confirm the delivery timeline for the pending items.

Regards,
Student Projects Coordinator
KCE Engineering College
717822p107@kce.ac.in""",
    },
    # 6. Tax dispute — GST exemption not applied (INV-2025-005)
    {
        "filename": "email_06_tax_dispute_kce.pdf",
        "sender": "717822p107@kce.ac.in",
        "subject": "GST Exemption Not Applied — Invoice INV-2025-005",
        "body": """To the Finance Department,

Re: Invoice INV-2025-005 — MATLAB & AutoCAD Licenses — INR 1,88,800

We have made full payment (RTGS: RTGS25041006634) as we needed the licenses urgently.
However, we wish to formally dispute the tax amount of INR 28,800 (18% GST).

As an educational institution, KCE Engineering College is exempt from GST on
software licenses supplied for academic purposes under GST Notification 12/2017.
Our GST exemption certificate number is EDU-GST-EXEMPT-KCE-2024-001.

The correct tax should be 0%, making the total INR 1,60,000 instead of INR 1,88,800.

We request:
1. A credit note for INR 28,800 (excess GST collected)
2. A revised tax invoice with 0% GST for ITC compliance

Please process this at the earliest.

Regards,
Finance Office
KCE Engineering College""",
    },
    # 7. Quality dispute + context shift (INV-2025-006 + also mentions INV-2025-004)
    {
        "filename": "email_07_quality_context_shift_kce.pdf",
        "sender": "717822p107@kce.ac.in",
        "subject": "Quality Issue — INV-2025-006 and Update on INV-2025-004",
        "body": """Dear Team,

I am writing about two separate matters:

MATTER 1 — Quality Dispute on INV-2025-006 (Canteen Supplies):
We received delivery on 14th February 2025. Upon inspection:
- 15 bags of rice (25kg each) were found to be of substandard quality —
  musty smell, presence of small stones, not fit for consumption.
- All 20 cans of cooking oil were received in acceptable condition.
- Disposable plates were fine.

We have made advance payment of INR 67,462.50 (UPI: UPI25046009920).
We are withholding the balance and requesting either replacement of the 15 rice
bags (INR 27,000) or a credit note for the same.

MATTER 2 — Raspberry Pi Delivery Update (INV-2025-004):
We still have not received the 10 Raspberry Pi units from our earlier order.
Our semester lab sessions start 1st March — urgent delivery needed.

Please address both matters separately.

Regards,
KCE Engineering College""",
    },
    # ── jeevadharani9384@gmail.com ────────────────────────────────────────────
    # 8. Early payment discount not applied (INV-2025-007)
    {
        "filename": "email_08_early_payment_discount_jeeva.pdf",
        "sender": "jeevadharani9384@gmail.com",
        "subject": "Early Payment Discount Not Applied — INV-2025-007",
        "body": """Hello,

I am writing about Invoice INV-2025-007 dated 5th January 2025 for INR 1,07,100.

Your invoice clearly states payment terms: 2/10 Net 30 — meaning a 2% discount
applies if payment is made within 10 days of invoice date (by 15th January 2025).

I made full payment of INR 1,07,100 on 13th January 2025 via UPI
(Reference: UPI25013002288) — well within the 10-day window.

The applicable early payment discount is 2% of INR 1,07,100 = INR 2,142.
Please issue a credit note for INR 2,142 against my account.

Thank you,
Jeeva Dharani
jeevadharani9384@gmail.com""",
    },
    # 9. Damaged goods on delivery (INV-2025-008)
    {
        "filename": "email_09_damaged_goods_jeeva.pdf",
        "sender": "jeevadharani9384@gmail.com",
        "subject": "Damaged Products Received — Invoice INV-2025-008",
        "body": """Dear Customer Support,

I am very disappointed to report that several items from Invoice INV-2025-008
(dated 18th January 2025) arrived damaged:

  - 2 out of 5 Mixer Grinders (750W): cracked lids, cannot be used
  - 1 out of 3 Induction Cooktops: shattered glass top, completely unusable

Damaged items value:
  - 2 Mixer Grinders: 2 x INR 3,200 = INR 6,400 (+ 18% GST = INR 7,552)
  - 1 Induction Cooktop: INR 2,800 (+ 18% GST = INR 3,304)
  Total damaged: INR 10,856

I have paid INR 25,376 via NEFT (Reference: NEFT25032007741) for the undamaged
items. I am withholding INR 13,800 until this is resolved.

I request either:
  a) Replacement of the 3 damaged units, OR
  b) A credit note for INR 10,856

Please arrange pickup of the damaged goods.

Regards,
Jeeva Dharani""",
    },
    # 10. Payment terms dispute (INV-2025-009)
    {
        "filename": "email_10_payment_terms_jeeva.pdf",
        "sender": "jeevadharani9384@gmail.com",
        "subject": "Incorrect Payment Terms on Invoice INV-2025-009",
        "body": """Hello Finance Team,

I am writing to dispute the payment due date on Invoice INV-2025-009 dated
15th February 2025 for INR 47,250.

The invoice states due date as 22nd February 2025 (Net 7 terms). However, our
agreement email dated 10th February 2025 (your reference: QUOTE-JD-2025-003)
clearly states payment terms of Net 30, which would make the due date 17th March 2025.

I am not in a position to pay within 7 days as this was not the agreed term.
I will make payment by 17th March 2025 as per the agreed Net 30 terms.

Please issue a corrected invoice with the correct due date of 17th March 2025.
Until then I am withholding payment to avoid any late payment penalties being
incorrectly applied.

Regards,
Jeeva Dharani
jeevadharani9384@gmail.com""",
    },
    # ── prakeshprakesh9345@gmail.com ──────────────────────────────────────────
    # 11. Short payment / pricing mismatch (INV-2025-010)
    {
        "filename": "email_11_pricing_short_payment_prakesh.pdf",
        "sender": "prakeshprakesh9345@gmail.com",
        "subject": "Rate Discrepancy — Invoice INV-2025-010",
        "body": """Hi,

I am Prakesh writing about Invoice INV-2025-010 for INR 25,200 (printing services).

The invoice charges INR 45 per sqft for flex banner printing (200 sqft = INR 9,000).
However, the rate quoted in your message dated 3rd January 2025 was INR 38 per sqft
(200 sqft = INR 7,600).

Overcharge on flex banners: INR 1,400 + 12% GST = INR 1,568.

I have paid INR 20,000 via UPI (UPI25018005531). I am withholding INR 5,200
until the rate is corrected and a revised invoice is issued.

Please check your records and revert.

Thanks,
Prakesh
prakeshprakesh9345@gmail.com""",
    },
    # 12. Multi-issue single email (INV-2025-011) — triggers inline disputes
    {
        "filename": "email_12_multi_issue_prakesh.pdf",
        "sender": "prakeshprakesh9345@gmail.com",
        "subject": "Multiple Issues — Invoice INV-2025-011 (Event Management)",
        "body": """Dear Team,

I have two separate disputes regarding Invoice INV-2025-011 for INR 1,98,240:

ISSUE 1 — LED Screen Not Delivered:
The invoice includes LED Screen 12x8ft rental for INR 15,000. This screen was
never delivered to the event venue on 25th January 2025. Our event coordinator
Mr. Suresh can confirm this. Please issue a credit note for INR 15,000 + GST
INR 2,700 = INR 17,700.

ISSUE 2 — Catering Count Wrong:
The invoice bills for 200 pax catering at INR 450 per head = INR 90,000.
Our event attendance was 150 pax as documented in the entry register. You should
only charge for 150 x INR 450 = INR 67,500. Overcharge: INR 22,500 + GST INR 4,050.

Total credit note required: INR 44,250 (INR 17,700 + INR 26,550).

I have paid the 50% advance (NEFT: NEFT25028009812). The balance payment is
withheld pending resolution of both issues.

Regards,
Prakesh
prakeshprakesh9345@gmail.com""",
    },
    # 13. Context shift email — starts with one issue, then raises another (INV-2025-012)
    {
        "filename": "email_13_context_shift_prakesh.pdf",
        "sender": "prakeshprakesh9345@gmail.com",
        "subject": "Payment Terms Wrong — INV-2025-012 / Also INV-2025-011 Update",
        "body": """Hello,

I want to raise two things in this email:

FIRST — Invoice INV-2025-012 (Courier Services):
The due date on this invoice is 12th February 2025 (Net 7). But our service
agreement clearly states Net 30 payment terms for all courier billing.
The correct due date should be 7th March 2025. I will not pay before that
and request a corrected invoice immediately.

SECOND — New Issue on INV-2025-011 (Photography):
I just reviewed the photos delivered from the event and the photography quality
is completely unacceptable — over 60% of photos are blurry, poorly lit, and
unusable for our company records. The photographer arrived 2 hours late and
left before the main ceremony.

I want a full refund of the photography charges (INR 12,000 + GST INR 2,160
= INR 14,160) in addition to the credits already requested for LED screen
and catering.

Please treat both as separate disputes.

Prakesh""",
    },
    # 14. Unverified ownership — prakesh trying to query KCE invoice (INV-2025-013)
    {
        "filename": "email_14_unverified_ownership_prakesh.pdf",
        "sender": "prakeshprakesh9345@gmail.com",
        "subject": "Query on Invoice INV-2025-013",
        "body": """Hi,

I wanted to check on Invoice INV-2025-013 for projector equipment.
Can you please share the current payment status and delivery schedule?

My email is prakeshprakesh9345@gmail.com.

Regards,
Prakesh""",
    },
    # 15. Follow-up clarification — no invoice details provided (clarification flow)
    {
        "filename": "email_15_no_invoice_clarification_jeeva.pdf",
        "sender": "jeevadharani9384@gmail.com",
        "subject": "Help needed with my recent purchase",
        "body": """Hello,

I bought some items from you last month but I am confused about the billing.
The amount charged to my account seems higher than what was discussed.

I don't have the invoice number handy right now. Can someone look into this
for me? My email is jeevadharani9384@gmail.com.

Thanks,
Jeeva""",
    },
    # 16. Payment not reflected follow-up (INV-2025-003 second email — threading test)
    {
        "filename": "email_16_followup_threading_baran.pdf",
        "sender": "baranekumar56@gmail.com",
        "subject": "Follow-up: Payment Still Not Reflected — INV-2025-003",
        "body": """Hello,

This is a follow-up to my earlier email regarding INV-2025-003.

It has now been 5 days and the invoice still shows as UNPAID in your system.
My bank reference is NEFT25051007782. The payment of INR 46,610 was debited
from my HDFC account on 20th February 2025.

I am attaching my bank statement as proof of payment. Please escalate this
urgently as I need the receipt for my GST filing deadline on 28th February.

This is becoming urgent — please respond today.

Regards,
Baran Kumar
baranekumar56@gmail.com""",
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
        ["FROM:", email_data["sender"]],
        ["TO:", "disputes@paisavasool.com"],
        ["SUBJECT:", email_data["subject"]],
        ["DATE:", datetime.now().strftime("%d %B %Y, %H:%M IST")],
    ]
    header_table = Table(header_data, colWidths=[1.2 * inch, 5 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#333333")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 0.3 * inch))

    divider = Table([[""]], colWidths=[6.5 * inch])
    divider.setStyle(
        TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#333333"))])
    )
    story.append(divider)
    story.append(Spacer(1, 0.2 * inch))

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
            story.append(Spacer(1, 0.1 * inch))

    doc.build(story)
    print(f"  created: {output_path.name}")


# =============================================================================
# DB Seeder
# =============================================================================


async def seed_database():
    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with AsyncSessionLocal() as session:
        print("\nSeeding dispute types...")
        for dt in DISPUTE_TYPES:
            result = await session.execute(
                text(
                    "SELECT dispute_type_id FROM dispute_type WHERE reason_name = :name"
                ),
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
                    "reason_name": dt["reason_name"],
                    "description": dt["description"],
                    "severity_level": dt["severity_level"],
                },
            )
            print(
                f"  DisputeType '{dt['reason_name']}' [{dt['severity_level']}] inserted"
            )
        await session.commit()
        print(f"  {len(DISPUTE_TYPES)} dispute types seeded")

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
                    "invoice_number": inv["invoice_number"],
                    "invoice_url": inv["invoice_url"],
                    "invoice_details": json.dumps(inv["invoice_details"]),
                },
            )
            print(f"  Invoice {inv['invoice_number']} inserted")

        print("\nSeeding payment details...")
        for pay in PAYMENTS:
            result = await session.execute(
                text(
                    "SELECT payment_detail_id FROM payment_detail WHERE payment_url = :url"
                ),
                {"url": pay["payment_url"]},
            )
            if result.fetchone():
                ref = pay["payment_details"].get(
                    "payment_reference", pay["payment_url"]
                )
                print(f"  Payment {ref} already exists, skipping.")
                continue
            await session.execute(
                text("""
                    INSERT INTO payment_detail (customer_id, invoice_number, payment_url, payment_details)
                    VALUES (:customer_id, :invoice_number, :payment_url, cast(:payment_details as jsonb))
                """),
                {
                    "customer_id": pay["customer_id"],
                    "invoice_number": pay["invoice_number"],
                    "payment_url": pay["payment_url"],
                    "payment_details": json.dumps(pay["payment_details"]),
                },
            )
            ref = pay["payment_details"].get("payment_reference", "N/A")
            ptype = pay["payment_details"].get("payment_type", "?")
            print(
                f"  Payment {ref} [{ptype}] for {pay['invoice_number']} → {pay['customer_id']}"
            )

        await session.commit()

    await engine.dispose()
    print("\nDatabase seeding complete!")


# =============================================================================
# Main
# =============================================================================


async def main():
    print("=" * 62)
    print("  Paisa Vasool — Seed Data (Real Email IDs)")
    print("=" * 62)
    print("""
Customers:
  baranekumar56@gmail.com       — 3 invoices (pricing, duplicate, payment not reflected)
  717822p107@kce.ac.in          — 3 invoices (goods not received, tax, quality/context shift)
  jeevadharani9384@gmail.com    — 3 invoices (early discount, damaged goods, payment terms)
  prakeshprakesh9345@gmail.com  — 3 invoices (short payment, multi-issue, context shift)

Special cases:
  Email 04  — No invoice (clarification flow)
  Email 07  — Multi-issue + context shift (inline + forked disputes)
  Email 12  — Two disputes in one email (inline dispute creation)
  Email 13  — Context shift mid-thread (forked dispute)
  Email 14  — Wrong sender for INV-2025-013 (UNVERIFIED ownership)
  Email 15  — No invoice details (clarification flow)
  Email 16  — Follow-up on same dispute (threading test)
    """)

    print(f"Generating {len(EMAILS)} email PDFs into ./{OUTPUT_DIR}/")
    for email_data in EMAILS:
        generate_email_pdf(email_data, OUTPUT_DIR / email_data["filename"])

    print(f"\nConnecting to DB: {DATABASE_URL.split('@')[-1]}")
    await seed_database()

    print("\n" + "=" * 62)
    print("  Done!")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
