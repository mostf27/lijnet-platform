import hashlib
from datetime import timezone
from decimal import ROUND_HALF_UP, Decimal

from fpdf import FPDF

from app.config import settings
from app.models import Order, PaymentStatus


def _safe(text: str | None) -> str:
    """Ensures strings render cleanly with built-in PDF Type1 fonts."""
    if not text:
        return ""
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _money(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _split_vat_inclusive(gross_etb: Decimal, vat_rate: Decimal) -> tuple[Decimal, Decimal]:
    """
    Extracts VAT-exclusive taxable base and 15% VAT amount from a VAT-inclusive
    Ethiopian retail price with penny-exact rounding.
    """
    divisor = Decimal("1.00") + vat_rate
    excl_vat = (gross_etb / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    vat_amount = gross_etb - excl_vat
    return excl_vat, vat_amount


class LijnetVatInvoicePDF(FPDF):
    def header(self) -> None:
        # Top Nile Teal Accent Bar
        self.set_fill_color(22, 105, 122)  # #16697A
        self.rect(0, 0, 210, 6, style="F")

        self.set_y(11)
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(22, 105, 122)
        self.cell(110, 7, _safe(settings.LEGAL_NAME.upper()), new_x="RIGHT", new_y="TOP")

        self.set_font("Helvetica", "B", 12)
        self.set_text_color(31, 41, 51)
        self.cell(80, 7, "VAT SALES INVOICE", align="R", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(85, 95, 105)
        self.cell(
            110,
            4.5,
            _safe(f"Brand: Lijnet Kids Store | {settings.SELLER_ADDRESS}"),
            new_x="RIGHT",
            new_y="TOP",
        )
        self.cell(
            80,
            4.5,
            "Ethiopian Ministry of Revenues (MoR) Compliant",
            align="R",
            new_x="LMARGIN",
            new_y="NEXT",
        )

        # Seller TIN & VAT Registration Strip
        self.ln(2)
        self.set_fill_color(250, 247, 242)  # #FAF7F2 Cream
        self.set_draw_color(220, 224, 228)
        self.set_font("Helvetica", "B", 8.5)
        self.set_text_color(31, 41, 51)
        tin_banner = (
            f"Seller TIN: {settings.SELLER_TIN}   |   "
            f"VAT Reg. No: {settings.SELLER_VAT_REG_NO}   |   "
            f"VAT Reg. Date: {settings.SELLER_VAT_REG_DATE}"
        )
        self.cell(
            190, 7, _safe(tin_banner), border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT"
        )
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-22)
        self.set_draw_color(220, 224, 228)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(2)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(100, 110, 120)
        self.cell(
            190,
            4,
            "Statutory Notice (Proclamation No. 1205/2020): Unopened, non-hygiene items may be returned within 7 calendar days of delivery.",
            align="C",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.cell(
            190,
            4,
            f"Lijnet Digital Commerce PLC | Addis Ababa, Ethiopia | Page {self.page_no()}",
            align="C",
        )


def generate_vat_invoice_pdf(order: Order, buyer_tin: str | None = None) -> bytes:
    """
    Builds a Ministry of Revenues (MoR) formatted 15% VAT receipt/invoice PDF
    for a given Lijnet order and returns raw PDF bytes.
    """
    pdf = LijnetVatInvoicePDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=25)
    pdf.add_page()

    # Fiscal Sequential Invoice Number & Timestamps
    fiscal_no = f"FS-{order.created_at.year}-{order.id:06d}"
    issued_dt = (
        order.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if order.created_at
        else ""
    )
    receipt_status = (
        "OFFICIAL 15% VAT TAX RECEIPT (PAID)"
        if order.payment_status == PaymentStatus.PAID
        else f"VAT TAX INVOICE ({order.payment_status.value})"
    )

    # Two-Column Metadata Box: Customer / Buyer Info (Left) & Fiscal Metadata (Right)
    start_y = pdf.get_y()
    addr = order.delivery_address

    # Left Box: Buyer / Recipient
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(22, 105, 122)
    pdf.cell(95, 5, "BILLED / DELIVERED TO:", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(31, 41, 51)
    pdf.cell(95, 5, _safe(addr.recipient_name), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8.5)
    pdf.cell(95, 4.5, _safe(f"Phone: {addr.recipient_phone}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        95,
        4.5,
        _safe(f"Addis Ababa, {addr.sub_city} Sub-City, Woreda {addr.woreda}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.multi_cell(92, 4.2, _safe(f"Landmark: {addr.landmark_description}"))
    if buyer_tin and buyer_tin.strip():
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(95, 4.5, _safe(f"Buyer TIN: {buyer_tin.strip()}"), new_x="LMARGIN", new_y="NEXT")
    left_end_y = pdf.get_y()

    # Right Box: Fiscal Invoice Numbers
    pdf.set_xy(108, start_y)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(22, 105, 122)
    pdf.cell(92, 5, "FISCAL & PAYMENT DETAILS:", align="R", new_x="LMARGIN", new_y="NEXT")

    meta_rows = [
        ("Fiscal Invoice No:", fiscal_no),
        ("Lijnet Order No:", order.order_number),
        ("Issue Date:", issued_dt),
        ("Payment Method:", order.payment_method.value),
        ("Gateway Ref:", order.tx_ref),
        ("Document Status:", receipt_status),
    ]
    for label, val in meta_rows:
        pdf.set_x(108)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(90, 100, 110)
        pdf.cell(36, 4.5, label, align="L")
        pdf.set_font("Helvetica", "B" if "Status" in label else "", 8)
        pdf.set_text_color(31, 41, 51)
        pdf.cell(56, 4.5, _safe(val), align="R", new_x="LMARGIN", new_y="NEXT")

    right_end_y = pdf.get_y()
    pdf.set_y(max(left_end_y, right_end_y) + 5)

    # Line Items Table Header
    pdf.set_fill_color(22, 105, 122)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8.5)

    col_widths = [10, 24, 72, 14, 23, 22, 25]  # Total = 190mm
    headers = ["#", "SKU", "Item Description", "Qty", "Unit (Pre-VAT)", "15% VAT", "Total (ETB)"]
    aligns = ["C", "L", "L", "C", "R", "R", "R"]

    for w, h, al in zip(col_widths, headers, aligns):
        pdf.cell(w, 7.5, h, border=1, fill=True, align=al)
    pdf.ln()

    # Table Rows (Extracting 15% VAT per line item)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(31, 41, 51)

    items_excl_sum = Decimal("0.00")
    items_vat_sum = Decimal("0.00")

    for idx, item in enumerate(order.items, start=1):
        line_gross = item.line_total_etb
        line_excl, line_vat = _split_vat_inclusive(line_gross, settings.VAT_RATE)
        unit_excl = (line_excl / Decimal(item.quantity)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        items_excl_sum += line_excl
        items_vat_sum += line_vat

        title_clean = _safe(item.product.title_en)
        if len(title_clean) > 42:
            title_clean = title_clean[:39] + "..."

        fill_row = idx % 2 == 0
        pdf.set_fill_color(248, 250, 252)

        row_vals = [
            str(idx),
            _safe(item.product.sku),
            title_clean,
            str(item.quantity),
            _money(unit_excl),
            _money(line_vat),
            _money(line_gross),
        ]
        for w, val, al in zip(col_widths, row_vals, aligns):
            pdf.cell(w, 6.5, val, border="LRB", fill=fill_row, align=al)
        pdf.ln()

    # Delivery Service Line Item (if > 0 ETB)
    deliv_excl, deliv_vat = _split_vat_inclusive(order.delivery_fee_etb, settings.VAT_RATE)
    if order.delivery_fee_etb > Decimal("0.00"):
        deliv_vals = [
            str(len(order.items) + 1),
            "DELIV-ADD",
            _safe(f"Addis Ababa Courier Delivery ({addr.sub_city})"),
            "1",
            _money(deliv_excl),
            _money(deliv_vat),
            _money(order.delivery_fee_etb),
        ]
        for w, val, al in zip(col_widths, deliv_vals, aligns):
            pdf.cell(w, 6.5, val, border="LRB", fill=False, align=al)
        pdf.ln()

    # Penny-Exact Tax Summary Block
    total_taxable_excl_vat = items_excl_sum + deliv_excl
    total_vat_15 = order.total_etb - total_taxable_excl_vat

    pdf.ln(4)
    summary_rows = [
        ("Total Taxable Amount (Excl. 15% VAT):", f"{_money(total_taxable_excl_vat)} ETB", False),
        ("Add: Ethiopian VAT @ 15.0%:", f"{_money(total_vat_15)} ETB", False),
        ("GRAND TOTAL (Incl. 15% VAT):", f"{_money(order.total_etb)} ETB", True),
    ]

    for label, val, is_grand in summary_rows:
        pdf.set_x(105)
        if is_grand:
            pdf.set_fill_color(22, 105, 122)
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.cell(55, 8, label, border=1, fill=True, align="R")
            pdf.cell(40, 8, val, border=1, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_fill_color(250, 247, 242)
            pdf.set_text_color(31, 41, 51)
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.cell(55, 6.5, label, border=1, fill=True, align="R")
            pdf.set_font("Helvetica", "", 8.5)
            pdf.cell(40, 6.5, val, border=1, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")

    # Fiscal Verification Signature Hash
    sig_source = f"{settings.SELLER_TIN}|{fiscal_no}|{order.order_number}|{order.total_etb:.2f}"
    verification_code = hashlib.sha256(sig_source.encode("utf-8")).hexdigest()[:16].upper()

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(85, 95, 105)
    pdf.cell(
        190,
        5,
        f"Electronic Fiscal Verification Code: LIJ-MOR-{verification_code}",
        align="L",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(
        190,
        4.5,
        "This is a computer-generated VAT invoice issued under Ethiopian Electronic Transaction Proclamation No. 1205/2020.",
        align="L",
    )

    return bytes(pdf.output())
