"""
Motor de métricas de "Panel ventas eCommerce": mismo patrón que `budgets.metrics`
(períodos semana/mes/año, buckets, rankings), pero sobre `PedidoOnline` (los
avisos de venta que llegan desde la tienda online, cualquier sub-marca), no
sobre `Presupuesto`. Mide el canal ecommerce por separado de la operación de
presupuestos a medida — son dos negocios con datos y granularidad distintos
que hoy conviven en el mismo admin (ver `budgets/admin.py::PedidoOnlineAdmin`
y el análisis que motivó esta vista).

Alcance v1 (decidido con el dueño, ver `AskUserQuestion` del plan):
- Solo cuentan como "venta" los pedidos con `estado=CONFIRMADO`. Pendiente
  todavía no es una venta resuelta; Rechazado nunca se facturó.
- La fecha que define "cuándo pasó la venta" es `order_created_at` (cuándo se
  creó la Order en la tienda real); si no vino en el payload del webhook
  (dato opcional), se cae a `received_at` (cuándo llegó el aviso a
  presupuestos3d) — así ningún pedido confirmado queda fuera de las series
  por faltarle ese dato.
- Sin margen/costo/beneficio: `PedidoOnline.items` solo guarda un snapshot
  (`product_name`, `quantity`, `unit_price`), sin SKU ni FK a `Producto`, así
  que no hay forma confiable de cruzarlo con el costeo real (un nombre de
  producto puede repetirse o cambiar). Tampoco hay "conversión" — este
  modelo no registra carritos/visitas, solo órdenes ya pagadas.
- Desglose por marca (`brand_slug`) además del total agregado, con filtro
  por marca: el negocio opera varias sub-marcas de ecommerce a la vez.
- Sin export a Excel/PDF en esta primera versión (sí lo tienen Panel de
  métricas/ventas/costos) — se agrega más adelante si hace falta.
"""

from collections import defaultdict
from decimal import Decimal

from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from .metrics import PERIODS, _money, _period_range_str, _qty, make_buckets  # noqa: F401 (reexport de formato)
from .models import PedidoOnline

ZERO = Decimal("0")


# ---------------------------------------------------------------------------
#  Consultas
# ---------------------------------------------------------------------------
def _confirmados_in_range(start, end, brand_slug=None):
    """Pedidos confirmados con `fecha_venta` (order_created_at u, si falta,
    received_at) en [start, end)."""
    qs = PedidoOnline.objects.filter(estado=PedidoOnline.Estado.CONFIRMADO).annotate(
        fecha_venta=Coalesce("order_created_at", "received_at")
    )
    if brand_slug:
        qs = qs.filter(brand_slug=brand_slug)
    return list(qs.filter(fecha_venta__gte=start, fecha_venta__lt=end))


def available_brands():
    """Marcas con al menos un pedido confirmado alguna vez — para el filtro,
    sin importar el período elegido (así no "desaparece" una marca del
    dropdown solo porque no vendió nada este mes)."""
    rows = (
        PedidoOnline.objects.filter(estado=PedidoOnline.Estado.CONFIRMADO)
        .values_list("brand_slug", "brand_name")
        .distinct()
    )
    by_slug = {}
    for slug, name in rows:
        if slug and slug not in by_slug:
            by_slug[slug] = name or slug
    return sorted(by_slug.items(), key=lambda kv: kv[1])


# ---------------------------------------------------------------------------
#  Cálculo de métricas
# ---------------------------------------------------------------------------
def build_ecommerce_metrics(period: str, now: "timezone.datetime" = None, brand_slug: str = None) -> dict:
    if period not in PERIODS:
        period = "month"
    now = now or timezone.now()

    buckets = make_buckets(period, now)
    cur = buckets[-1]
    range_start = buckets[0]["start"]
    range_end = buckets[-1]["end"]

    pedidos_all = _confirmados_in_range(range_start, range_end, brand_slug)
    cur_pedidos = [p for p in pedidos_all if cur["start"] <= p.fecha_venta < cur["end"]]

    # --- Serie de facturación por bucket (para el gráfico) ---
    serie = []
    for b in buckets:
        total = sum(
            (p.total_amount for p in pedidos_all if b["start"] <= p.fecha_venta < b["end"]),
            ZERO,
        )
        serie.append({"label": b["label"], "total": total})

    facturacion = sum((p.total_amount for p in cur_pedidos), ZERO)
    n_pedidos = len(cur_pedidos)
    ticket = (facturacion / n_pedidos) if n_pedidos else ZERO
    dias_periodo = max((cur["end"] - cur["start"]).days, 1)
    venta_diaria_promedio = facturacion / dias_periodo

    # Ranking de productos (por nombre snapshot, no hay FK a Producto) y de
    # clientes (por email; invitados sin email se agrupan aparte).
    by_qty = defaultdict(lambda: ZERO)
    by_money = defaultdict(lambda: ZERO)
    cli_money = defaultdict(lambda: ZERO)
    cli_qty = defaultdict(lambda: ZERO)
    marca_money = defaultdict(lambda: ZERO)
    marca_count = defaultdict(int)
    marca_name = {}

    sin_email_label = str(gettext("Sin email (invitado)"))
    for p in cur_pedidos:
        cliente = p.customer_email or sin_email_label
        cli_money[cliente] += p.total_amount
        marca_money[p.brand_slug] += p.total_amount
        marca_count[p.brand_slug] += 1
        marca_name[p.brand_slug] = p.brand_name or p.brand_slug
        for it in p.items or []:
            name = it.get("product_name") or str(gettext("(sin nombre)"))
            try:
                qty = Decimal(str(it.get("quantity") or 0))
                unit_price = Decimal(str(it.get("unit_price") or 0))
            except Exception:
                qty = ZERO
                unit_price = ZERO
            by_qty[name] += qty
            by_money[name] += qty * unit_price
            cli_qty[cliente] += qty

    top_productos_qty = sorted(by_qty.items(), key=lambda kv: kv[1], reverse=True)[:10]
    top_productos_money = sorted(by_money.items(), key=lambda kv: kv[1], reverse=True)[:10]
    top_clientes = sorted(cli_money.items(), key=lambda kv: kv[1], reverse=True)[:10]
    top_clientes_qty = sorted(cli_qty.items(), key=lambda kv: kv[1], reverse=True)[:10]

    por_marca = sorted(
        (
            {
                "slug": slug,
                "name": marca_name.get(slug, slug),
                "count": marca_count[slug],
                "total": total,
            }
            for slug, total in marca_money.items()
        ),
        key=lambda r: r["total"],
        reverse=True,
    )

    return {
        "period": period,
        "period_label": PERIODS[period]["label"],
        "cur_start": cur["start"],
        "cur_end": cur["end"],
        "now": now,
        "brand_slug": brand_slug or "",
        "serie": serie,
        "facturacion": facturacion,
        "n_pedidos": n_pedidos,
        "ticket": ticket,
        "venta_diaria_promedio": venta_diaria_promedio,
        "top_productos_qty": top_productos_qty,
        "top_productos_money": top_productos_money,
        "top_clientes": top_clientes,
        "top_clientes_qty": top_clientes_qty,
        "por_marca": por_marca,
    }


# ---------------------------------------------------------------------------
#  Contexto para el template (incluye datos del gráfico en JSON)
# ---------------------------------------------------------------------------
def template_context(m: dict) -> dict:
    import json

    fact_chart = {
        "labels": [s["label"] for s in m["serie"]],
        "data": [float(s["total"]) for s in m["serie"]],
    }
    return {
        # `_metrics_base.html` (compartida con Métricas/Panel de ventas/Panel
        # de costos) siempre instancia estas tres variables JS aunque esta
        # página no las use — sin estos defaults, la variable faltante
        # resuelve a "" en el template y rompe el <script> entero (ni
        # siquiera se dibujaría el gráfico de facturación).
        "maq_chart_json": "{}",
        "costos_chart_json": "{}",
        "costos_extra_json": "[]",
        "period": m["period"],
        "period_label": m["period_label"],
        "range_str": _period_range_str(m),
        "brand_slug": m["brand_slug"],
        "facturacion": _money(m["facturacion"]),
        "n_pedidos": m["n_pedidos"],
        "ticket": _money(m["ticket"]),
        "venta_diaria_promedio": _money(m["venta_diaria_promedio"]),
        "top_productos_qty": [{"name": n, "qty": _qty(q)} for n, q in m["top_productos_qty"]],
        "top_productos_money": [
            {"name": n, "money": _money(v)} for n, v in m["top_productos_money"]
        ],
        "top_clientes": [{"name": n, "money": _money(v)} for n, v in m["top_clientes"]],
        "top_clientes_qty": [{"name": n, "qty": _qty(q)} for n, q in m["top_clientes_qty"]],
        "por_marca": [
            {
                "slug": r["slug"],
                "name": r["name"],
                "count": r["count"],
                "total": _money(r["total"]),
            }
            for r in m["por_marca"]
        ],
        "fact_chart_json": json.dumps(fact_chart),
    }
