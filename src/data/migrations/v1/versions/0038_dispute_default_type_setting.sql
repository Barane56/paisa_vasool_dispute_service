-- seed dispute types for llm


INSERT INTO dispute_type (reason_name, description, severity_level, is_active) VALUES
-- ── PRICING & BILLING (9) ─────────────────────────────────────────────────────
 
('Pricing Mismatch',
 'Customer claims the price charged differs from the agreed or quoted price.',
 'HIGH', true),
 
('Agreed Price Not Honoured',
 'The price charged on the invoice does not match the price agreed in the contract, purchase order, or written quotation provided to the customer.',
 'HIGH', true),
 
('Unit Price Discrepancy',
 'The per-unit price on the invoice differs from the rate card, framework agreement, or previously invoiced rate for the same product or service.',
 'HIGH', true),
 
('Unauthorised Price Increase',
 'Customer disputes a unilateral price increase applied without prior written notice or agreement as required by the service contract.',
 'HIGH', true),
 
('Discount Not Applied',
 'A negotiated, contractual, or promotional discount was not reflected on the invoice despite being agreed in writing or per trade terms.',
 'MEDIUM', true),
 
('Early Payment Discount',
 'Customer paid within the early payment window and applied the agreed discount but the supplier has not accepted the deduction.',
 'MEDIUM', true),
 
('Rebate Not Credited',
 'A volume rebate, annual rebate, or retrospective discount earned by the customer has not been credited against the outstanding balance.',
 'MEDIUM', true),
 
('Penalty Charge Disputed',
 'Customer disputes a late payment penalty, interest charge, or surcharge applied to their account, claiming it is incorrect or not contractually justified.',
 'MEDIUM', true),
 
('Overcharge on Services',
 'The total amount billed for services exceeds the agreed scope, hourly rate, or fixed fee with no change order or variation order raised to authorise the excess.',
 'HIGH', true),
 
-- ── QUANTITY & LINE ITEMS (7) ─────────────────────────────────────────────────
 
('Incorrect Quantity',
 'Customer disputes the quantity billed, claiming it does not match the quantity of goods or services actually delivered, accepted, or recorded on the goods receipt note.',
 'HIGH', true),
 
('Partial Delivery Billed in Full',
 'The invoice charges for the full order quantity but only a partial delivery was made. Customer will pay only for the quantity received.',
 'HIGH', true),
 
('Duplicate Line Item',
 'The same product, service, or charge appears more than once on the invoice or across multiple invoices for the same delivery event.',
 'MEDIUM', true),
 
('Wrong Product Billed',
 'The invoice references a product code, SKU, or service description that does not correspond to what was ordered or delivered.',
 'MEDIUM', true),
 
('Cancelled Order Still Invoiced',
 'An order that was formally cancelled by the customer prior to fulfilment has been invoiced. Customer denies liability for the charge.',
 'HIGH', true),
 
('Return Not Credited',
 'Goods were returned to the supplier with a return authorisation but a credit note has not been issued and the original invoice remains outstanding.',
 'HIGH', true),
 
('Consignment Adjustment Dispute',
 'Customer disputes the reconciliation of consignment stock, claiming the quantity consumed or sold differs from the supplier''s billing record.',
 'MEDIUM', true),
 
-- ── TAX (8) ───────────────────────────────────────────────────────────────────
 
('Tax Dispute',
 'Customer disputes the tax rate, tax amount, or tax exemption applied on the invoice.',
 'HIGH', true),
 
('Wrong Tax Rate Applied',
 'The tax rate used on the invoice (GST, VAT, sales tax) is incorrect for the applicable jurisdiction, product classification, or customer exemption status.',
 'HIGH', true),
 
('Tax Error',
 'An error in the tax calculation results in an incorrect tax amount being applied to the invoice.',
 'MEDIUM', true),
 
('Tax Exemption Not Honoured',
 'Customer holds a valid tax exemption certificate or zero-rating applies to the supply but tax was charged in full on the invoice.',
 'HIGH', true),
 
('HSN / SAC Code Mismatch',
 'The HSN or SAC code on the invoice is incorrect, causing the wrong tax rate to be applied or blocking the customer''s input tax credit claim.',
 'MEDIUM', true),
 
('GST Not Charged — ITC Blocked',
 'Customer requires GST to be charged on the invoice to claim input tax credit but the invoice was raised as exempt or the GSTIN is missing.',
 'MEDIUM', true),
 
('TDS Deduction',
 'Customer has deducted TDS at source and remitted it to the tax authority but the net payment has not been reconciled against the invoice correctly.',
 'LOW', true),
 
('Tax Invoice Format Non-Compliant',
 'The invoice does not meet statutory requirements such as missing GSTIN, IRN, QR code, or e-invoice mandate fields, preventing the customer from processing it.',
 'MEDIUM', true),
 
-- ── PAYMENT APPLICATION & RECONCILIATION (9) ─────────────────────────────────
 
('Payment Not Reflected',
 'Customer claims payment was made but it has not been applied to the invoice on record.',
 'HIGH', true),
 
('Payment Not Applied to Correct Invoice',
 'Customer made a payment that has been applied to the wrong invoice or account, leaving the correct invoice appearing unpaid.',
 'MEDIUM', true),
 
('Short Payment',
 'Customer has paid less than the invoiced amount without prior agreement.',
 'MEDIUM', true),
 
('Overpayment Not Refunded',
 'Customer paid more than the invoiced amount and has requested a refund or credit note that has not been issued.',
 'MEDIUM', true),
 
('Advance Payment Not Adjusted',
 'A prepayment or advance was made against this contract or order but has not been deducted from the current invoice.',
 'MEDIUM', true),
 
('Payment Allocation Dispute',
 'Customer disagrees with how the supplier has allocated a lump-sum payment across multiple open invoices, resulting in incorrect ageing of the balance.',
 'LOW', true),
 
('Bank Charges Deducted',
 'Customer deducted international bank transfer or intermediary fees from the payment. Dispute is over which party bears these charges per contract terms.',
 'LOW', true),
 
('Currency Difference',
 'Invoice was raised in a currency other than agreed, or customer applied a different exchange rate than the invoice rate resulting in a shortfall.',
 'MEDIUM', true),
 
('Payment Status Inquiry',
 'Customer is enquiring about whether a specific payment has been received and applied to their account.',
 'LOW', true),
 
-- ── DELIVERY & GOODS (6) ──────────────────────────────────────────────────────
 
('Goods Not Received',
 'Customer disputes the invoice because the goods or services were not delivered.',
 'HIGH', true),
 
('Goods Delivered Damaged',
 'Goods were received in a damaged condition. Customer is disputing the invoice pending replacement, repair, or a credit note for the damaged items.',
 'HIGH', true),
 
('Short Shipment',
 'The shipment received contained fewer units than the packing list and invoice indicated. Customer will only pay for the quantity confirmed as received.',
 'HIGH', true),
 
('Wrong Goods Delivered',
 'The items delivered do not match the items ordered. Customer refuses to pay until the correct goods are delivered or the invoice is revised.',
 'HIGH', true),
 
('Delivery Outside Agreed Window',
 'Goods or services were delivered outside the contractually agreed delivery window and the customer is claiming a penalty deduction or refusing payment.',
 'MEDIUM', true),
 
('POD Not Available',
 'Supplier cannot produce a signed proof of delivery. Customer denies receipt and will not pay until delivery is confirmed.',
 'HIGH', true),
 
-- ── SERVICE & CONTRACT (9) ────────────────────────────────────────────────────
 
('Quality Dispute',
 'Customer received goods or services but disputes the quality or completeness of delivery.',
 'HIGH', true),
 
('Service Quality',
 'Customer disputes the quality, scope, or completeness of a service delivered, affecting the billed amount.',
 'HIGH', true),
 
('SLA Breach Deduction',
 'Customer is deducting a service level agreement penalty from the invoice because agreed KPIs, uptime targets, or delivery metrics were not met.',
 'HIGH', true),
 
('Scope Creep Not Authorised',
 'The invoice includes charges for work performed beyond the agreed scope of the contract without a signed change order or variation notice.',
 'HIGH', true),
 
('Milestone Not Achieved',
 'A milestone-based invoice has been raised but the customer disputes that the milestone conditions have been fully met, withholding payment until sign-off.',
 'HIGH', true),
 
('Warranty Claim Offset',
 'Customer is offsetting the invoice balance against an open warranty claim or defect rectification cost that the supplier has not yet resolved.',
 'MEDIUM', true),
 
('Contract Terminated — Invoice Invalid',
 'The contract was terminated before the invoiced period. Customer denies liability for services or goods billed after the termination effective date.',
 'HIGH', true),
 
('Retention Amount Dispute',
 'Customer is withholding a contractual retention amount beyond the agreed retention period, or disputes the retention percentage applied.',
 'MEDIUM', true),
 
('Payment Terms Dispute',
 'Customer disputes the payment due date, credit period, or agreed payment terms on the invoice.',
 'HIGH', true),
 
-- ── INVOICE ADMINISTRATION (6) ────────────────────────────────────────────────
 
('Wrong Entity Billed',
 'The invoice was raised against the wrong legal entity, subsidiary, or cost centre within the customer''s organisation.',
 'MEDIUM', true),
 
('Wrong Address or GSTIN on Invoice',
 'The billing address, ship-to address, or GSTIN on the invoice is incorrect, preventing the customer from processing it through their accounts payable system.',
 'LOW', true),
 
('Purchase Order Number Missing',
 'The invoice does not reference the customer''s PO number, which is mandatory for payment processing under the customer''s procurement policy.',
 'LOW', true),
 
('PO Number Mismatch',
 'The PO number on the invoice does not match any open or valid purchase order in the customer''s system, causing the invoice to be blocked.',
 'MEDIUM', true),
 
('Invoice Period Incorrect',
 'The service period or billing cycle stated on the invoice does not correspond to the actual period of delivery, causing an accounting period mismatch.',
 'LOW', true),
 
('Proforma Invoice Sent Instead of Tax Invoice',
 'Customer received a proforma or draft invoice instead of a valid tax invoice, which cannot be processed for payment or used to claim input tax credit.',
 'LOW', true),
 
-- ── CREDIT & DEBIT NOTES (3) ─────────────────────────────────────────────────
 
('Credit Note Not Issued',
 'A credit note was promised or is contractually due for returns, overbilling, or dispute resolution but has not been raised, leaving the invoice balance overstated.',
 'MEDIUM', true),
 
('Credit Note Amount Incorrect',
 'A credit note was issued but the value does not fully compensate for the agreed deduction, leaving a residual disputed balance.',
 'MEDIUM', true),
 
('Debit Note Raised by Customer',
 'Customer has raised a formal debit note against the supplier for a deduction they consider valid. Supplier disputes the deduction basis or amount.',
 'MEDIUM', true),
 
-- ── DUPLICATE & GENERAL (4) ──────────────────────────────────────────────────
 
('Duplicate Invoice',
 'Customer has received two or more invoices for the same transaction, delivery, or billing period and will only pay once.',
 'MEDIUM', true),
 
('Already Paid — Payment Proof Available',
 'Customer asserts the invoice has already been paid in full and can provide bank confirmation, remittance advice, or payment reference as evidence.',
 'HIGH', true),
 
('Invoice Not Received',
 'Customer states they never received the original invoice and is requesting a copy before agreeing to any payment terms or due date.',
 'LOW', true),
 
('General Clarification',
 'General inquiries and clarification requests that do not constitute a formal dispute.',
 'LOW', true)
 
ON CONFLICT (reason_name)
DO UPDATE SET
    description    = EXCLUDED.description,
    severity_level = EXCLUDED.severity_level,
    is_active      = EXCLUDED.is_active;
 