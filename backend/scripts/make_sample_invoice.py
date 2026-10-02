from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = (
    Path(__file__).resolve().parents[1] / "app" / "sample_docs" / "lumora-invoice-INV-2026-0917.pdf"
)

LINES = [
    ("Lumora Business plan, 30 seats x 40.00 USD, monthly", "1,200.00"),
    ("Priority support add-on", "40.00"),
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=A4)
    c.setTitle("Invoice INV-2026-0917")
    y = A4[1] - 25 * mm
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, y, "Lumora Inc. - Invoice")
    c.setFont("Helvetica", 10)
    for line in [
        "Invoice number: INV-2026-0917",
        "Issue date: 2026-09-22",
        "Billed to: Sunrise Home Care, aisha@sunrisehome.example",
        "Currency: USD",
    ]:
        y -= 7 * mm
        c.drawString(20 * mm, y, line)
    y -= 12 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Description")
    c.drawRightString(190 * mm, y, "Amount (USD)")
    c.setFont("Helvetica", 10)
    for description, amount in LINES:
        y -= 7 * mm
        c.drawString(20 * mm, y, description)
        c.drawRightString(190 * mm, y, amount)
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Total due")
    c.drawRightString(190 * mm, y, "1,240.00")
    c.save()
    print(OUT)


if __name__ == "__main__":
    main()
