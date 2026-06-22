"""
Generación del PDF de presupuesto para el cliente.

Muestra solo lo que el cliente debe ver (pieza, cantidad, precio unitario y
total), nunca el desglose interno de costos (material, máquina, mano de obra).
"""

import base64
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.template.loader import render_to_string
from django.utils import timezone

# --- Datos del negocio (editá estos valores con tus datos reales) ---
BUSINESS_NAME = "3darg"
BUSINESS_TAGLINE = "Impresión 3D"
BUSINESS_CONTACT = "3darg1@gmail.com"
QUOTE_VALIDITY_DAYS = 7

# Logo del negocio (se embebe en el PDF como data URI para no depender de rutas).
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo3darg.jpeg"


def _logo_data_uri() -> str:
    """Devuelve el logo como data URI base64, o '' si no se encuentra."""
    try:
        data = LOGO_PATH.read_bytes()
    except FileNotFoundError:
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def format_money(value) -> str:
    """Formatea un monto al estilo argentino: 8.000,00"""
    value = Decimal(value or 0).quantize(Decimal("0.01"))
    entero, _, dec = f"{value:.2f}".partition(".")
    negativo = entero.startswith("-")
    entero = entero.lstrip("-")
    # separador de miles con punto
    partes = []
    while len(entero) > 3:
        partes.insert(0, entero[-3:])
        entero = entero[:-3]
    partes.insert(0, entero)
    entero_fmt = ".".join(partes)
    signo = "-" if negativo else ""
    return f"{signo}{entero_fmt},{dec}"


def build_budget_context(budget) -> dict:
    includes = [str(line.aggregate) for line in budget.aggregate_lines.all()]
    today = timezone.localdate()
    return {
        "business_name": BUSINESS_NAME,
        "business_tagline": BUSINESS_TAGLINE,
        "business_contact": BUSINESS_CONTACT,
        "logo_data_uri": _logo_data_uri(),
        "budget": budget,
        "date_str": today.strftime("%d/%m/%Y"),
        "quantity": budget.quantity,
        "unit_price_str": format_money(budget.unit_price),
        "total_str": format_money(budget.total),
        "includes": includes,
        "validity_days": QUOTE_VALIDITY_DAYS,
    }


def render_budget_pdf(budget) -> bytes:
    """Devuelve los bytes del PDF del presupuesto para el cliente."""
    # Import diferido: solo se necesita al generar un PDF.
    from xhtml2pdf import pisa

    html = render_to_string("budgets/budget_pdf.html", build_budget_context(budget))
    buffer = BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer, encoding="utf-8")
    if result.err:
        raise RuntimeError("No se pudo generar el PDF del presupuesto.")
    return buffer.getvalue()


def budget_pdf_filename(budget) -> str:
    return f"presupuesto_{budget.pk}.pdf"
