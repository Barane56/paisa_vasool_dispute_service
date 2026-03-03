"""
seed_data.py
============
Run this script to:
  1. Generate 8 realistic customer email PDFs (saved to ./sample_emails/)
  2. Seed the database with matching InvoiceData and PaymentDetail records

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

# ─────────────────────────────────────────────────────────────────────────────
# Sample data
# ─────────────────────────────────────────────────────────────────────────────

INVOICES = [
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
                {"description": "Office Furniture Set", "qty": 10, "unit_price": 5000.00, "total": 50000.00},
                {"description": "Ergonomic Chairs", "qty": 20, "unit_price": 2500.00, "total": 50000.00},
            ],
            "subtotal": 100000.00,
            "tax_amount": 18000.00,
            "total_amount": 118000.00,
            "currency": "INR",
            "payment_terms": "Net 30",
        },
    },
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
                {"description": "Annual Software License", "qty": 1, "unit_price": 250000.00, "total": 250000.00},
                {"description": "Implementation Support (hrs)", "qty": 40, "unit_price": 3000.00, "total": 120000.00},
            ],
            "subtotal": 370000.00,
            "tax_amount": 66600.00,
            "total_amount": 436600.00,
            "currency": "INR",
            "payment_terms": "Net 30",
        },
    },
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
                {"description": "Spare Parts Kit", "qty": 5, "unit_price": 12000.00, "total": 60000.00},
                {"description": "Installation Service", "qty": 1, "unit_price": 25000.00, "total": 25000.00},
            ],
            "subtotal": 485000.00,
            "tax_amount": 87300.00,
            "total_amount": 572300.00,
            "currency": "INR",
            "payment_terms": "Net 45",
        },
    },
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
                {"description": "Point of Sale Systems", "qty": 15, "unit_price": 35000.00, "total": 525000.00},
                {"description": "Annual Maintenance Contract", "qty": 1, "unit_price": 45000.00, "total": 45000.00},
            ],
            "subtotal": 570000.00,
            "tax_amount": 102600.00,
            "total_amount": 672600.00,
            "currency": "INR",
            "payment_terms": "Net 30",
        },
    },
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
                {"description": "Fleet Management Software", "qty": 1, "unit_price": 180000.00, "total": 180000.00},
                {"description": "GPS Devices", "qty": 50, "unit_price": 4500.00, "total": 225000.00},
            ],
            "subtotal": 405000.00,
            "tax_amount": 72900.00,
            "total_amount": 477900.00,
            "currency": "INR",
            "payment_terms": "Net 30",
        },
    },
]

PAYMENTS = [
    # Full payment for INV-2024-001
    {
        "customer_id": "acme",
        "invoice_number": "INV-2024-001",
        "payment_url": "https://storage.example.com/payments/PAY-2024-001.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-001",
            "payment_date": "2024-11-25",
            "amount_paid": 118000.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT24325001234",
            "invoice_number": "INV-2024-001",
            "customer_id": "acme",
            "status": "CLEARED",
        },
    },
    # SHORT payment for INV-2024-002 (paid less)
    {
        "customer_id": "techsoft",
        "invoice_number": "INV-2024-002",
        "payment_url": "https://storage.example.com/payments/PAY-2024-002.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-002",
            "payment_date": "2024-11-30",
            "amount_paid": 400000.00,
            "payment_mode": "RTGS",
            "bank_reference": "RTGS24330005678",
            "invoice_number": "INV-2024-002",
            "customer_id": "techsoft",
            "status": "CLEARED",
            "note": "Customer paid 400000 against invoice of 436600 - short by 36600",
        },
    },
    # Full payment for INV-2024-003
    {
        "customer_id": "globaltraders",
        "invoice_number": "INV-2024-003",
        "payment_url": "https://storage.example.com/payments/PAY-2024-003.pdf",
        "payment_details": {
            "payment_reference": "PAY-2024-003",
            "payment_date": "2024-12-08",
            "amount_paid": 572300.00,
            "payment_mode": "NEFT",
            "bank_reference": "NEFT24342009012",
            "invoice_number": "INV-2024-003",
            "customer_id": "globaltraders",
            "status": "CLEARED",
        },
    },
    # No payment yet for INV-2024-004 (Sunrise) - dispute about incorrect quantity
    # No payment yet for INV-2024-005 (Metro) - dispute about pricing
]

# ─────────────────────────────────────────────────────────────────────────────
# Email content definitions
# ─────────────────────────────────────────────────────────────────────────────

EMAILS = [
    # 1. Short payment dispute
    {
        "filename": "email_01_short_payment_techsoft.pdf",
        "sender": "accounts@techsoft.com",
        "subject": "Re: Invoice INV-2024-002 - Payment Clarification",
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

    # 2. Pricing mismatch
    {
        "filename": "email_02_pricing_dispute_metro.pdf",
        "sender": "finance@metro.com",
        "subject": "Dispute - Invoice INV-2024-005 Pricing Mismatch",
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
CFO, Metro Logistics
finance@metro.com""",
    },

    # 3. Duplicate invoice complaint
    {
        "filename": "email_03_duplicate_invoice_acme.pdf",
        "sender": "ap@acmecorp.com",
        "subject": "Duplicate Invoice Received - INV-2024-001",
        "body": """Hello,

I am writing to bring to your attention that we have received what appears to be 
a duplicate invoice for Invoice Number INV-2024-001.

Details of original invoice already paid:
  - Invoice No: INV-2024-001
  - Invoice Date: 1st November 2024
  - Amount: INR 1,18,000
  - Payment Made: 25th November 2024
  - Our Payment Reference: PAY-2024-001
  - Bank NEFT Reference: NEFT24325001234

We received a second copy of the same invoice via email on 5th December 2024 
with a payment due notice. Please note that this invoice has already been paid 
in full on 25th November 2024.

Kindly check your records and confirm that the payment has been received and 
applied correctly. Please also ensure we are removed from any payment reminders 
for this invoice.

Thank you,
Anita Desai
Accounts Payable
Acme Corp
ap@acmecorp.com | +91-22-6789-0123""",
    },

    # 4. Incorrect quantity dispute
    {
        "filename": "email_04_incorrect_quantity_sunrise.pdf",
        "sender": "accounts@sunrise-retail.com",
        "subject": "Invoice INV-2024-004 - Incorrect Quantity Billed",
        "body": """Dear Sir/Madam,

I am writing regarding Invoice Number INV-2024-004 dated 15th November 2024 
for INR 6,72,600.

We have identified a quantity discrepancy in this invoice:

Point of Sale Systems:
  - Quantity Invoiced: 15 units
  - Quantity Actually Delivered (as per our GRN-SR-2024-112): 12 units
  - 3 units were short delivered

The 3 missing POS units were never received at our warehouse. Our Goods Receipt 
Note (GRN-SR-2024-112) dated 20th November 2024, signed by our warehouse manager 
Mr. Suresh Nair, confirms delivery of only 12 units.

We request:
1. A credit note for 3 units x INR 35,000 = INR 1,05,000 + GST
2. Either delivery of the remaining 3 units OR permanent credit

We are withholding payment of the full invoice amount until this is resolved.

Please contact us at the earliest.

Regards,
Vikram Mehta
Head of Accounts
Sunrise Retail Pvt Ltd
accounts@sunrise-retail.com""",
    },

    # 5. Tax error dispute
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
  - Industrial Equipment (HSN 8428): 12% GST applies = INR 48,000 + INR 7,200 = INR 55,200
  - Spare Parts (HSN 8487): 18% GST applies = INR 60,000 x 18% = INR 10,800
  - Installation Service (SAC 9987): 18% GST applies = INR 25,000 x 18% = INR 4,500

Correct total GST should be: INR 70,500
Invoiced GST: INR 87,300
Excess GST charged: INR 16,800

Please issue a credit note for INR 16,800 and a revised GST invoice so we can 
claim accurate input tax credit.

Warm regards,
Deepak Agarwal
Finance Controller
Global Traders Ltd""",
    },

    # 6. Payment status inquiry
    {
        "filename": "email_06_payment_status_acme.pdf",
        "sender": "ap@acmecorp.com",
        "subject": "Payment Status Inquiry - INV-2024-001",
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

    # 7. General clarification
    {
        "filename": "email_07_clarification_techsoft.pdf",
        "sender": "accounts@techsoft.com",
        "subject": "Clarification Required - Invoice INV-2024-002 Line Items",
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

    # 8. Service quality dispute
    {
        "filename": "email_08_service_quality_metro.pdf",
        "sender": "finance@metro.com",
        "subject": "Service Quality Dispute - Invoice INV-2024-005",
        "body": """Dear Accounts Team,

In addition to the pricing dispute raised in our previous email regarding 
Invoice INV-2024-005, we also want to formally raise a service quality concern.

The GPS devices supplied under this invoice (50 units) have shown a defect rate 
of 20% (10 units non-functional) within the first 2 weeks of deployment. 
This is unacceptable given the 6-month warranty promised at the time of sale.

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

This matter is urgent as it is affecting our fleet operations.

Regards,
Priya Sharma
CFO, Metro Logistics""",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# PDF Generator
# ─────────────────────────────────────────────────────────────────────────────

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

    # Header table
    header_data = [
        ["FROM:", email_data["sender"]],
        ["TO:", "disputes@paisavasool.com"],
        ["SUBJECT:", email_data["subject"]],
        ["DATE:", datetime.now().strftime("%d %B %Y, %H:%M IST")],
    ]
    header_table = Table(header_data, colWidths=[1.2*inch, 5*inch])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#333333")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.3*inch))

    # Divider
    divider = Table([[""]], colWidths=[6.5*inch])
    divider.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#333333")),
    ]))
    story.append(divider)
    story.append(Spacer(1, 0.2*inch))

    # Body
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
    print(f"  ✓ Created: {output_path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# DB Seeder
# ─────────────────────────────────────────────────────────────────────────────

async def seed_database():
    engine = create_async_engine(DATABASE_URL, echo=False)
    AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        print("\n📦 Seeding invoices...")
        for inv in INVOICES:
            # Check if already exists
            result = await session.execute(
                text("SELECT invoice_id FROM invoice_data WHERE invoice_number = :num"),
                {"num": inv["invoice_number"]}
            )
            existing = result.fetchone()
            if existing:
                print(f"  ⚠ Invoice {inv['invoice_number']} already exists, skipping.")
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
                }
            )
            print(f"  ✓ Invoice {inv['invoice_number']} inserted")

        print("\n💳 Seeding payment details...")
        for pay in PAYMENTS:
            result = await session.execute(
                text("""
                    SELECT payment_detail_id FROM payment_detail
                    WHERE customer_id = :cid AND invoice_number = :inv
                """),
                {"cid": pay["customer_id"], "inv": pay["invoice_number"]}
            )
            existing = result.fetchone()
            if existing:
                print(f"  ⚠ Payment for {pay['invoice_number']} already exists, skipping.")
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
                }
            )
            print(f"  ✓ Payment for {pay['invoice_number']} ({pay['customer_id']}) inserted")

        await session.commit()

    await engine.dispose()
    print("\n✅ Database seeding complete!")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  Paisa Vasool - Sample Data Generator")
    print("=" * 60)

    print(f"\n📄 Generating {len(EMAILS)} email PDFs → ./{OUTPUT_DIR}/")
    for email_data in EMAILS:
        generate_email_pdf(email_data, OUTPUT_DIR / email_data["filename"])

    print(f"\n🗄  Connecting to: {DATABASE_URL.split('@')[-1]}")
    await seed_database()

    print("\n" + "=" * 60)
    print("  DONE! Next steps:")
    print("=" * 60)
    print(f"\n  Email PDFs are in: ./{OUTPUT_DIR}/")
    print("\n  Ingest emails via API (requires auth token):")
    print("  POST http://localhost:8002/api/v1/emails/ingest")
    print("  Form fields: file=<pdf>, sender_email=<email>, subject=<subject>")
    print("\n  Example curl:")
    print("""
  curl -X POST http://localhost:8002/api/v1/emails/ingest \\
    -H "Authorization: Bearer <your_token>" \\
    -F "file=@sample_emails/email_01_short_payment_techsoft.pdf" \\
    -F "sender_email=accounts@techsoft.com" \\
    -F "subject=Re: Invoice INV-2024-002 - Payment Clarification"
  """)


if __name__ == "__main__":
    asyncio.run(main())