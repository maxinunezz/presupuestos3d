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
QUOTE_VALIDITY_DAYS = 30

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


def build_presupuesto_context(presupuesto) -> dict:
    today = timezone.localdate()
    items = []
    for item in presupuesto.items.select_related("producto").all():
        items.append(
            {
                "name": item.producto.name,
                "description": item.producto.description,
                "quantity": item.quantity,
                "unit_price_str": format_money(item.effective_unit_price),
                "line_total_str": format_money(item.line_total),
            }
        )
    return {
        "business_name": BUSINESS_NAME,
        "business_tagline": BUSINESS_TAGLINE,
        "business_contact": BUSINESS_CONTACT,
        "logo_data_uri": _logo_data_uri(),
        "presupuesto": presupuesto,
        "date_str": today.strftime("%d/%m/%Y"),
        "items": items,
        "fixed_cost_str": format_money(presupuesto.fixed_cost),
        "has_fixed_cost": presupuesto.fixed_cost and presupuesto.fixed_cost > 0,
        "total_str": format_money(presupuesto.total),
        "validity_days": QUOTE_VALIDITY_DAYS,
    }


def render_presupuesto_pdf(presupuesto) -> bytes:
    """Devuelve los bytes del PDF del presupuesto para el cliente."""
    from xhtml2pdf import pisa

    html = render_to_string(
        "budgets/presupuesto_pdf.html", build_presupuesto_context(presupuesto)
    )
    buffer = BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer, encoding="utf-8")
    if result.err:
        raise RuntimeError("No se pudo generar el PDF del presupuesto.")
    return buffer.getvalue()


def presupuesto_pdf_filename(presupuesto) -> str:
    return f"presupuesto_{presupuesto.pk}.pdf"
