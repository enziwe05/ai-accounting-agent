"""
Turn an invoice (the dict from invoices.get_invoice) into a clean PDF the owner
can send to their customer.

Business details — the name, contact info, VAT number and banking line that go at
the top — come from the environment, so each company's instance prints its own
letterhead without any code change:

    BUSINESS_NAME, BUSINESS_EMAIL, BUSINESS_PHONE, BUSINESS_ADDRESS,
    BUSINESS_VAT_NO, BUSINESS_BANK_DETAILS

Layout uses reportlab. Colours follow the project's look: deep indigo headings
with a gold accent, so the invoice reads as a real business document.
"""

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

INDIGO = colors.HexColor("#141A3C")
GOLD = colors.HexColor("#F2B544")
GREY = colors.HexColor("#6B7280")
LIGHT = colors.HexColor("#F4F5F8")


def _business() -> dict:
    return {
        "name": os.getenv("BUSINESS_NAME", "Your Business"),
        "email": os.getenv("BUSINESS_EMAIL", ""),
        "phone": os.getenv("BUSINESS_PHONE", ""),
        "address": os.getenv("BUSINESS_ADDRESS", ""),
        "vat_no": os.getenv("BUSINESS_VAT_NO", ""),
        "bank": os.getenv("BUSINESS_BANK_DETAILS", ""),
    }


def _money(currency: str, amount) -> str:
    """Format money the local way: R2 849.00, E500.00 (space as thousands sep)."""
    from currency import symbol as _sym
    sym = _sym(currency)
    # Known symbols hug the number (R2 849.00); unknown codes get a space (XAF 500.00).
    joiner = "" if sym and sym != currency else " "
    return f"{sym}{joiner}{amount:,.2f}".replace(",", " ")


def render_invoice_pdf(invoice: dict, out_path: str | Path) -> Path:
    """Write `invoice` to a PDF at out_path and return the path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    biz = _business()
    currency = invoice.get("currency", "ZAR")

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    small = ParagraphStyle("small", parent=normal, fontSize=8.5, textColor=GREY,
                           leading=12)
    biz_name = ParagraphStyle("bizname", parent=normal, fontSize=17,
                              textColor=INDIGO, leading=20, spaceAfter=2,
                              fontName="Helvetica-Bold")
    title = ParagraphStyle("title", parent=normal, fontSize=26, textColor=INDIGO,
                           alignment=2, fontName="Helvetica-Bold")
    label = ParagraphStyle("label", parent=normal, fontSize=8, textColor=GOLD,
                           fontName="Helvetica-Bold", spaceAfter=1)
    body = ParagraphStyle("body", parent=normal, fontSize=10, leading=14)

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {invoice.get('invoice_number', '')}",
    )
    story = []

    # --- Header band: business (left) + INVOICE title (right) ---------------
    biz_lines = [Paragraph(biz["name"], biz_name)]
    for key in ("address", "phone", "email"):
        if biz[key]:
            biz_lines.append(Paragraph(biz[key], small))
    if biz["vat_no"]:
        biz_lines.append(Paragraph(f"VAT No: {biz['vat_no']}", small))

    header = Table(
        [[biz_lines, Paragraph("INVOICE", title)]],
        colWidths=[95 * mm, 79 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [header, Spacer(1, 4 * mm)]

    # Gold accent rule under the header.
    rule = Table([[""]], colWidths=[174 * mm], rowHeights=[2])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GOLD)]))
    story += [rule, Spacer(1, 6 * mm)]

    # --- Bill-to + invoice meta ---------------------------------------------
    bill_to = [Paragraph("BILL TO", label),
               Paragraph(invoice.get("customer", ""), body)]
    if invoice.get("customer_email"):
        bill_to.append(Paragraph(invoice["customer_email"], small))
    if invoice.get("customer_phone"):
        bill_to.append(Paragraph(invoice["customer_phone"], small))

    meta_rows = [
        ["Invoice #", invoice.get("invoice_number", "")],
        ["Issue date", str(invoice.get("issue_date", ""))],
        ["Due date", str(invoice.get("due_date", "") or "—")],
    ]
    if invoice.get("view_status"):
        meta_rows.append(["Status", invoice["view_status"].title()])
    meta = Table(meta_rows, colWidths=[24 * mm, 42 * mm])
    meta.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), GREY),
        ("TEXTCOLOR", (1, 0), (1, -1), INDIGO),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))

    info = Table([[bill_to, meta]], colWidths=[108 * mm, 66 * mm])
    info.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [info, Spacer(1, 8 * mm)]

    # --- Line-items table ----------------------------------------------------
    data = [["Description", "Qty", "Unit price", "Amount"]]
    line_items = invoice.get("line_items") or []
    if line_items:
        for item in line_items:
            qty = item.get("quantity")
            unit = item.get("unit_price")
            amt = item.get("amount")
            if amt is None and qty and unit:
                amt = float(qty) * float(unit)
            data.append([
                Paragraph(item.get("description", ""), body),
                f"{qty:g}" if qty else "",
                _money(currency, float(unit)) if unit is not None else "",
                _money(currency, float(amt)) if amt is not None else "",
            ])
    else:
        # No itemisation — one line describing the work, at the subtotal amount.
        desc = invoice.get("notes") or "Services rendered"
        data.append([Paragraph(desc, body), "", "",
                     _money(currency, invoice["subtotal_amount"])])

    items_table = Table(data, colWidths=[92 * mm, 16 * mm, 32 * mm, 34 * mm])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ("FONTSIZE", (1, 1), (-1, -1), 9.5),
    ]))
    story += [items_table, Spacer(1, 4 * mm)]

    # --- Totals (right-aligned block) ---------------------------------------
    totals_rows = [["Subtotal", _money(currency, invoice["subtotal_amount"])]]
    if invoice.get("vat_amount"):
        totals_rows.append(["VAT (15%)", _money(currency, invoice["vat_amount"])])
    totals_rows.append(["Total due", _money(currency, invoice["total_amount"])])

    totals = Table(totals_rows, colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        # Emphasise the final "Total due" row.
        ("BACKGROUND", (0, -1), (-1, -1), INDIGO),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 11),
        ("TOPPADDING", (0, -1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
    ]))
    story += [totals, Spacer(1, 10 * mm)]

    # --- Payment details + thank-you ----------------------------------------
    if biz["bank"]:
        story.append(Paragraph("PAYMENT DETAILS", label))
        for line in biz["bank"].split("|"):
            story.append(Paragraph(line.strip(), body))
        story.append(Spacer(1, 5 * mm))

    if invoice.get("notes"):
        story.append(Paragraph("NOTES", label))
        story.append(Paragraph(invoice["notes"], body))
        story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Thank you for your business.", small))

    doc.build(story)
    return out_path
