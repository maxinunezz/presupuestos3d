"""
Motor del panel de gastos.

Calcula, para un período (un mes o un año completo), el total de gastos
operativos, el desglose por categoría, la evolución mensual, el comparativo con
el período anterior, el compromiso mensual recurrente (run-rate), el resultado
operativo contra las ventas y el control de topes por categoría.

Todo el dinero se suma en Python (los totales de ventas son @property, no
columnas). A la escala del negocio es exacto y rápido.
"""

from datetime import date, datetime, time
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext

from .models import AreaResponsable, CategoriaGasto, Gasto, TopeGasto

ZERO = Decimal("0")


def _meses():
    return [
        "",
        gettext("Enero"),
        gettext("Febrero"),
        gettext("Marzo"),
        gettext("Abril"),
        gettext("Mayo"),
        gettext("Junio"),
        gettext("Julio"),
        gettext("Agosto"),
        gettext("Septiembre"),
        gettext("Octubre"),
        gettext("Noviembre"),
        gettext("Diciembre"),
    ]


# ---------------------------------------------------------------------------
#  Rangos de fecha
# ---------------------------------------------------------------------------
def _month_range(year: int, month: int):
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def _year_range(year: int):
    return date(year, 1, 1), date(year + 1, 1, 1)


def _aware(d: date) -> datetime:
    return timezone.make_aware(
        datetime.combine(d, time.min), timezone.get_current_timezone()
    )


def _prev_month(year: int, month: int):
    return (year - 1, 12) if month == 1 else (year, month - 1)


# ---------------------------------------------------------------------------
#  Sumas base
# ---------------------------------------------------------------------------
def _gastos_total(start: date, end: date, tipo: str | None = None) -> Decimal:
    qs = Gasto.objects.filter(fecha__gte=start, fecha__lt=end)
    if tipo:
        qs = qs.filter(tipo=tipo)
    montos = qs.values_list("monto", flat=True)
    return sum((Decimal(m) for m in montos), ZERO)


def gastos_operativos_total(start: date, end: date) -> Decimal:
    """Total de gastos de tipo OPERATIVO (estructura del negocio) en
    [start, end). Excluye los EXTRAORDINARIOS (puntuales, no representativos:
    viajes, imprevistos) para que no distorsionen el resultado. Punto de
    entrada público para otros módulos (lo usa budgets.metrics para armar el
    resultado Ingresos/Costos/Gastos/Beneficio del panel de Métricas)."""
    return _gastos_total(start, end, tipo=Gasto.Tipo.OPERATIVO)


def gastos_extraordinarios_total(start: date, end: date) -> Decimal:
    """Total de gastos de tipo EXTRAORDINARIO (puntuales, no representativos)
    en [start, end). Se muestra por separado, informativo: no entra en el
    resultado operativo vs ventas."""
    return _gastos_total(start, end, tipo=Gasto.Tipo.EXTRAORDINARIO)


def _ventas_total(start: date, end: date) -> Decimal:
    """Facturación aprobada (Presupuesto.total) en el rango de fechas."""
    from budgets.models import Presupuesto

    qs = (
        Presupuesto.objects.filter(
            approved_at__gte=_aware(start), approved_at__lt=_aware(end)
        )
        .exclude(status=Presupuesto.Status.CANCELLED)
        .exclude(para_stock=True)  # reposición de stock interno, no es venta real
        .prefetch_related("items__producto")
    )
    return sum((p.total for p in qs), ZERO)


def available_years():
    """Años con datos (de gastos) + el año actual, de mayor a menor."""
    years = set(
        Gasto.objects.dates("fecha", "year").values_list("fecha__year", flat=True)
    )
    years.add(timezone.localdate().year)
    return sorted(years, reverse=True)


# ---------------------------------------------------------------------------
#  Cálculo principal
# ---------------------------------------------------------------------------
def build_gastos_metrics(year: int, month) -> dict:
    """
    `month` = 1..12 para ver un mes; None (o 0) para ver el año completo.
    Devuelve un dict con todos los números crudos (Decimals/listas).
    """
    today = timezone.localdate()
    is_month_view = bool(month)
    meses = _meses()

    if is_month_view:
        start, end = _month_range(year, month)
        months_in_period = 1
        period_label = f"{meses[month]} {year}"
        prev_y, prev_m = _prev_month(year, month)
        prev_start, prev_end = _month_range(prev_y, prev_m)
        prev_label = f"{meses[prev_m]} {prev_y}"
    else:
        start, end = _year_range(year)
        months_in_period = 12
        period_label = gettext("Año %(year)s") % {"year": year}
        prev_start, prev_end = _year_range(year - 1)
        prev_label = gettext("Año %(year)s") % {"year": year - 1}

    gastos = list(
        Gasto.objects.filter(fecha__gte=start, fecha__lt=end).select_related(
            "categoria", "area"
        )
    )

    # --- Total y desglose por categoría ---
    total_gastos = sum((Decimal(g.monto) for g in gastos), ZERO)
    n_gastos = len(gastos)

    # Las categorías son un modelo editable (CategoriaGasto), no un choice fijo:
    # se listan TODAS las que existan (aunque tengan $0 en el período), igual
    # que antes con el enum completo.
    todas_categorias = list(CategoriaGasto.objects.all())
    por_cat = {
        cat.pk: {"total": ZERO, "count": 0, "operativo": ZERO, "extraordinario": ZERO}
        for cat in todas_categorias
    }
    for g in gastos:
        monto = Decimal(g.monto)
        por_cat[g.categoria_id]["total"] += monto
        por_cat[g.categoria_id]["count"] += 1
        if g.tipo == Gasto.Tipo.OPERATIVO:
            por_cat[g.categoria_id]["operativo"] += monto
        else:
            por_cat[g.categoria_id]["extraordinario"] += monto
    categorias = [
        {
            "value": cat.pk,
            "label": cat.nombre,
            "total": por_cat[cat.pk]["total"],
            "count": por_cat[cat.pk]["count"],
            "operativo": por_cat[cat.pk]["operativo"],
            "extraordinario": por_cat[cat.pk]["extraordinario"],
            "pct": (por_cat[cat.pk]["total"] / total_gastos * 100) if total_gastos else ZERO,
        }
        for cat in todas_categorias
    ]
    categorias.sort(key=lambda c: c["total"], reverse=True)

    # --- Desglose por área responsable ---
    # El área es opcional (no todos los gastos la tienen asignada): los gastos
    # sin área se agrupan aparte en "Sin área" para que el total del desglose
    # siga cuadrando con `total_gastos`.
    SIN_AREA = None
    todas_areas = list(AreaResponsable.objects.all())
    por_area = {
        area.pk: {"total": ZERO, "count": 0, "operativo": ZERO, "extraordinario": ZERO}
        for area in todas_areas
    }
    por_area[SIN_AREA] = {"total": ZERO, "count": 0, "operativo": ZERO, "extraordinario": ZERO}
    for g in gastos:
        monto = Decimal(g.monto)
        por_area[g.area_id]["total"] += monto
        por_area[g.area_id]["count"] += 1
        if g.tipo == Gasto.Tipo.OPERATIVO:
            por_area[g.area_id]["operativo"] += monto
        else:
            por_area[g.area_id]["extraordinario"] += monto
    areas = [
        {
            "value": area.pk,
            "label": area.nombre,
            "total": por_area[area.pk]["total"],
            "count": por_area[area.pk]["count"],
            "operativo": por_area[area.pk]["operativo"],
            "extraordinario": por_area[area.pk]["extraordinario"],
            "pct": (por_area[area.pk]["total"] / total_gastos * 100) if total_gastos else ZERO,
        }
        for area in todas_areas
    ]
    if por_area[SIN_AREA]["count"]:
        areas.append(
            {
                "value": SIN_AREA,
                "label": gettext("Sin área"),
                "total": por_area[SIN_AREA]["total"],
                "count": por_area[SIN_AREA]["count"],
                "operativo": por_area[SIN_AREA]["operativo"],
                "extraordinario": por_area[SIN_AREA]["extraordinario"],
                "pct": (
                    por_area[SIN_AREA]["total"] / total_gastos * 100
                    if total_gastos
                    else ZERO
                ),
            }
        )
    areas.sort(key=lambda a: a["total"], reverse=True)

    # --- Evolución: 12 meses del año seleccionado ---
    serie = []
    for m in range(1, 13):
        ms, me = _month_range(year, m)
        tot = sum(
            (Decimal(g.monto) for g in gastos if ms <= g.fecha < me), ZERO
        )
        serie.append({"label": meses[m][:3], "month": m, "total": tot})

    # --- Comparativo con el período anterior (#3) ---
    prev_total = _gastos_total(prev_start, prev_end)
    if prev_total:
        variacion_pct = (total_gastos - prev_total) / prev_total * 100
    else:
        variacion_pct = None

    # Variación por categoría.
    prev_gastos = list(Gasto.objects.filter(fecha__gte=prev_start, fecha__lt=prev_end))
    prev_por_cat = {cat.pk: ZERO for cat in todas_categorias}
    for g in prev_gastos:
        prev_por_cat[g.categoria_id] += Decimal(g.monto)
    for c in categorias:
        pv = prev_por_cat.get(c["value"], ZERO)
        c["prev"] = pv
        c["var_pct"] = ((c["total"] - pv) / pv * 100) if pv else None

    # Meses con datos del período (para promediar compromiso/gasto mensual).
    # En vista de un mes es 1; en vista anual, los meses ya transcurridos del año.
    if year == today.year:
        meses_transcurridos = today.month
    elif year < today.year:
        meses_transcurridos = 12
    else:
        meses_transcurridos = 1
    run_rate_divisor = 1 if is_month_view else meses_transcurridos

    # --- Run-rate recurrente (#2) ---
    # Modelo basado en eventos: una suscripción mensual aparece como un gasto por
    # mes. El compromiso mensual = equivalente mensual sumado / meses con datos
    # (no por los 12 fijos), si no, una vista anual de año en curso lo subestima.
    recurrentes = [g for g in gastos if g.es_recurrente]
    run_rate_mensual = (
        sum((g.monthly_equivalent for g in recurrentes), ZERO) / run_rate_divisor
    ).quantize(Decimal("0.01"))
    # Proyección anual: en vista de un mes se puede afinar con las cuotas
    # cargadas (`cuota_actual`/`cuotas_totales`) para no seguir proyectando un
    # gasto en cuotas (ej: una impresora en 12 pagos) más allá de cuando
    # termina de pagarse. En vista anual (varios meses/eventos mezclados) se
    # mantiene la proyección simple (promedio × 12).
    if is_month_view and recurrentes:
        total_proyeccion = ZERO
        for g in recurrentes:
            restantes = g.meses_restantes
            meses_proyectados = 12 if restantes is None else min(12, restantes)
            total_proyeccion += g.monthly_equivalent * meses_proyectados
        run_rate_anual = total_proyeccion.quantize(Decimal("0.01"))
    else:
        run_rate_anual = (run_rate_mensual * 12).quantize(Decimal("0.01"))
    recurrentes_detalle = sorted(
        (
            {
                "concepto": g.concepto,
                "categoria": g.categoria.nombre,
                "periodicidad": g.get_periodicidad_display(),
                "monthly": g.monthly_equivalent,
                "cuotas": (
                    f"{g.cuota_actual}/{g.cuotas_totales}"
                    if g.cuotas_totales and g.cuota_actual
                    else "—"
                ),
            }
            for g in recurrentes
        ),
        key=lambda r: r["monthly"],
        reverse=True,
    )

    # --- Operativo vs extraordinario ---
    # Los EXTRAORDINARIOS son gastos puntuales y no representativos (viajes,
    # imprevistos): se muestran aparte para no distorsionar el resultado
    # operativo. `total_gastos` arriba sigue siendo la salida real de caja
    # (todos los tipos); estos dos son el desglose.
    gastos_operativos = sum(
        (Decimal(g.monto) for g in gastos if g.tipo == Gasto.Tipo.OPERATIVO), ZERO
    )
    gastos_extraordinarios = total_gastos - gastos_operativos
    extraordinarios_detalle = sorted(
        (
            {
                "concepto": g.concepto,
                "categoria": g.categoria.nombre,
                "fecha": g.fecha,
                "monto": Decimal(g.monto),
            }
            for g in gastos
            if g.tipo == Gasto.Tipo.EXTRAORDINARIO
        ),
        key=lambda r: r["monto"],
        reverse=True,
    )

    # --- Resultado operativo vs ventas (#1) ---
    # Usa solo gastos OPERATIVOS: un gasto extraordinario (viaje, imprevisto)
    # no debe hundir la lectura de salud operativa del negocio.
    ventas = _ventas_total(start, end)
    resultado = ventas - gastos_operativos
    gastos_sobre_ventas = (gastos_operativos / ventas * 100) if ventas else None
    # Resultado final: transparencia total, incluye los extraordinarios.
    resultado_final = ventas - total_gastos

    # --- Topes por categoría (#4) ---
    topes = {t.categoria_id: Decimal(t.monto_mensual) for t in TopeGasto.objects.all()}
    topes_rows = []
    for c in categorias:
        tope_mensual = topes.get(c["value"], ZERO)
        if tope_mensual <= 0:
            continue
        tope_periodo = tope_mensual * months_in_period
        pct = (c["total"] / tope_periodo * 100) if tope_periodo else ZERO
        topes_rows.append(
            {
                "label": c["label"],
                "gasto": c["total"],
                "tope": tope_periodo,
                "pct": pct,
                "excedido": c["total"] > tope_periodo,
                "restante": tope_periodo - c["total"],
            }
        )

    # --- Promedio mensual y acumulado anual (#6) ---
    y_start, y_end = _year_range(year)
    acumulado_anual = _gastos_total(y_start, y_end)
    promedio_mensual = (
        acumulado_anual / meses_transcurridos if meses_transcurridos else ZERO
    ).quantize(Decimal("0.01"))

    return {
        "year": year,
        "month": month or 0,
        "is_month_view": is_month_view,
        "period_label": period_label,
        "prev_label": prev_label,
        "range_str": f"{start.strftime('%d/%m/%Y')} – {(end).strftime('%d/%m/%Y')}",
        "total_gastos": total_gastos,
        "n_gastos": n_gastos,
        "categorias": categorias,
        "areas": areas,
        "serie": serie,
        "prev_total": prev_total,
        "variacion_pct": variacion_pct,
        "run_rate_mensual": run_rate_mensual,
        "run_rate_anual": run_rate_anual,
        "recurrentes_detalle": recurrentes_detalle,
        "gastos_operativos": gastos_operativos,
        "gastos_extraordinarios": gastos_extraordinarios,
        "extraordinarios_detalle": extraordinarios_detalle,
        "ventas": ventas,
        "resultado": resultado,
        "resultado_final": resultado_final,
        "gastos_sobre_ventas": gastos_sobre_ventas,
        "topes_rows": topes_rows,
        "acumulado_anual": acumulado_anual,
        "promedio_mensual": promedio_mensual,
        "meses_transcurridos": meses_transcurridos,
    }


# ---------------------------------------------------------------------------
#  Formato para el template
# ---------------------------------------------------------------------------
def _money(v) -> str:
    from budgets.pdf import format_money

    return "$ " + format_money(v)


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%" if v < 0 or v > 0 else "0,0%"


def _pct_plain(v) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}%"


def _chart_segments(items) -> dict:
    """Arma un donut de un solo anillo pero con el gasto operativo y el
    extraordinario de cada fila como dos porciones consecutivas (mismo color,
    la extraordinaria más clara) — así el tamaño real de cada porción sigue
    sumando el total del período, y se ve a simple vista cuánto de cada
    área/categoría es extraordinario."""
    labels, values, color_index, is_extra = [], [], [], []
    idx = 0
    for it in items:
        if it["total"] <= 0:
            continue
        if it["operativo"] > 0:
            labels.append(str(it["label"]))
            values.append(float(it["operativo"]))
            color_index.append(idx)
            is_extra.append(False)
        if it["extraordinario"] > 0:
            labels.append(str(it["label"]))
            values.append(float(it["extraordinario"]))
            color_index.append(idx)
            is_extra.append(True)
        idx += 1
    return {"labels": labels, "values": values, "colorIndex": color_index, "isExtra": is_extra}


def template_context(m: dict) -> dict:
    import json

    cat_chart = _chart_segments(m["categorias"])
    area_chart = _chart_segments(m["areas"])
    serie_chart = {
        "labels": [s["label"] for s in m["serie"]],
        "data": [float(s["total"]) for s in m["serie"]],
    }

    categorias = [
        {
            "label": c["label"],
            "total": _money(c["total"]),
            "count": c["count"],
            "pct": _pct_plain(c["pct"]),
            "var_pct": _pct(c["var_pct"]),
            "var_up": (c["var_pct"] is not None and c["var_pct"] > 0),
            "extraordinario": _money(c["extraordinario"]) if c["extraordinario"] > 0 else "—",
            "tiene_extraordinario": c["extraordinario"] > 0,
        }
        for c in m["categorias"]
    ]
    areas = [
        {
            "label": a["label"],
            "total": _money(a["total"]),
            "count": a["count"],
            "pct": _pct_plain(a["pct"]),
            "extraordinario": _money(a["extraordinario"]) if a["extraordinario"] > 0 else "—",
            "tiene_extraordinario": a["extraordinario"] > 0,
        }
        for a in m["areas"]
    ]
    recurrentes = [
        {
            "concepto": r["concepto"],
            "categoria": r["categoria"],
            "periodicidad": r["periodicidad"],
            "monthly": _money(r["monthly"]),
            "cuotas": r["cuotas"],
        }
        for r in m["recurrentes_detalle"]
    ]
    extraordinarios = [
        {
            "concepto": e["concepto"],
            "categoria": e["categoria"],
            "fecha": e["fecha"].strftime("%d/%m/%Y"),
            "monto": _money(e["monto"]),
        }
        for e in m["extraordinarios_detalle"]
    ]
    topes = [
        {
            "label": t["label"],
            "gasto": _money(t["gasto"]),
            "tope": _money(t["tope"]),
            "pct": _pct_plain(t["pct"]),
            "excedido": t["excedido"],
            "restante": _money(t["restante"]),
        }
        for t in m["topes_rows"]
    ]

    return {
        "period_label": m["period_label"],
        "prev_label": m["prev_label"],
        "range_str": m["range_str"],
        "total_gastos": _money(m["total_gastos"]),
        "n_gastos": m["n_gastos"],
        "categorias": categorias,
        "areas": areas,
        "variacion_pct": _pct(m["variacion_pct"]),
        "variacion_up": (m["variacion_pct"] is not None and m["variacion_pct"] > 0),
        "prev_total": _money(m["prev_total"]),
        "run_rate_mensual": _money(m["run_rate_mensual"]),
        "run_rate_anual": _money(m["run_rate_anual"]),
        "recurrentes": recurrentes,
        "gastos_operativos": _money(m["gastos_operativos"]),
        "gastos_extraordinarios": _money(m["gastos_extraordinarios"]),
        "hay_extraordinarios": m["gastos_extraordinarios"] > 0,
        "extraordinarios": extraordinarios,
        "ventas": _money(m["ventas"]),
        "resultado": _money(m["resultado"]),
        "resultado_positivo": m["resultado"] >= 0,
        "resultado_final": _money(m["resultado_final"]),
        "resultado_final_positivo": m["resultado_final"] >= 0,
        "gastos_sobre_ventas": _pct_plain(m["gastos_sobre_ventas"]),
        "topes": topes,
        "acumulado_anual": _money(m["acumulado_anual"]),
        "promedio_mensual": _money(m["promedio_mensual"]),
        "meses_transcurridos": m["meses_transcurridos"],
        "cat_chart_json": json.dumps(cat_chart),
        "area_chart_json": json.dumps(area_chart),
        "serie_chart_json": json.dumps(serie_chart),
    }


# ---------------------------------------------------------------------------
#  Export a Excel (#5)
# ---------------------------------------------------------------------------
def export_xlsx(m: dict):
    """Arma el .xlsx del período de gastos y devuelve (filename, bytes)."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="111111")
    head_font = Font(color="FFFFFF", bold=True)
    title_font = Font(bold=True, size=14)

    def style_header(ws, row, ncols):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row, column=c)
            cell.fill = head_fill
            cell.font = head_font

    def autosize(ws):
        for col in ws.columns:
            width = max(
                (len(str(c.value)) for c in col if c.value is not None), default=10
            )
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(
                width + 3, 50
            )

    # --- Hoja 1: Resumen ---
    ws = wb.active
    ws.title = gettext("Resumen")
    ws["A1"] = gettext("Gastos 3darg — %(period)s (%(range)s)") % {
        "period": m["period_label"],
        "range": m["range_str"],
    }
    ws["A1"].font = title_font
    rows = [
        (gettext("Total de gastos"), float(m["total_gastos"])),
        (gettext("Cantidad de gastos"), m["n_gastos"]),
        (gettext("Total %(prev)s") % {"prev": m["prev_label"]}, float(m["prev_total"])),
        (
            gettext("Variación vs período anterior"),
            (f"{m['variacion_pct']:.1f}%" if m["variacion_pct"] is not None else "—"),
        ),
        ("", ""),
        (gettext("Compromiso mensual recurrente"), float(m["run_rate_mensual"])),
        (gettext("Proyección anual recurrente"), float(m["run_rate_anual"])),
        ("", ""),
        (gettext("Gastos operativos del período"), float(m["gastos_operativos"])),
        (gettext("Gastos extraordinarios del período"), float(m["gastos_extraordinarios"])),
        ("", ""),
        (gettext("Ventas del período"), float(m["ventas"])),
        (gettext("Resultado operativo (ventas − gastos operativos)"), float(m["resultado"])),
        (
            gettext("Gastos operativos sobre ventas"),
            (
                f"{m['gastos_sobre_ventas']:.1f}%"
                if m["gastos_sobre_ventas"] is not None
                else "—"
            ),
        ),
        (
            gettext("Resultado final (ventas − todos los gastos)"),
            float(m["resultado_final"]),
        ),
        ("", ""),
        (gettext("Acumulado año %(year)s") % {"year": m["year"]}, float(m["acumulado_anual"])),
        (gettext("Promedio mensual"), float(m["promedio_mensual"])),
    ]
    r = 3
    for label, value in rows:
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=value)
        if value == "" and label:
            ws.cell(row=r, column=1).font = Font(bold=True)
        r += 1
    autosize(ws)

    # --- Hoja 2: Por categoría ---
    ws2 = wb.create_sheet(gettext("Por categoría"))
    ws2.append([
        gettext("Categoría"),
        gettext("Gasto"),
        gettext("% del total"),
        gettext("Cantidad"),
        gettext("Extraordinario"),
        m["prev_label"],
    ])
    style_header(ws2, 1, 6)
    for c in m["categorias"]:
        ws2.append(
            [
                str(c["label"]),
                float(c["total"]),
                float(c["pct"]),
                c["count"],
                float(c["extraordinario"]),
                float(c["prev"]),
            ]
        )
    autosize(ws2)

    # --- Hoja 3: Evolución mensual (con gráfico) ---
    ws3 = wb.create_sheet(gettext("Evolución"))
    ws3.append([gettext("Mes"), gettext("Gasto")])
    style_header(ws3, 1, 2)
    for s in m["serie"]:
        ws3.append([s["label"], float(s["total"])])
    chart = BarChart()
    chart.title = gettext("Gastos por mes — %(year)s") % {"year": m["year"]}
    chart.y_axis.title = "$"
    data = Reference(ws3, min_col=2, min_row=1, max_row=1 + len(m["serie"]))
    cats = Reference(ws3, min_col=1, min_row=2, max_row=1 + len(m["serie"]))
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.legend = None
    ws3.add_chart(chart, "D2")
    autosize(ws3)

    # --- Hoja 4: Recurrentes ---
    ws4 = wb.create_sheet(gettext("Recurrentes"))
    ws4.append([
        gettext("Concepto"),
        gettext("Categoría"),
        gettext("Periodicidad"),
        gettext("Cuota"),
        gettext("Equivalente mensual"),
    ])
    style_header(ws4, 1, 5)
    for r in m["recurrentes_detalle"]:
        ws4.append(
            [
                r["concepto"],
                str(r["categoria"]),
                str(r["periodicidad"]),
                r["cuotas"],
                float(r["monthly"]),
            ]
        )
    autosize(ws4)

    # --- Hoja 5: Extraordinarios ---
    ws4b = wb.create_sheet(gettext("Extraordinarios"))
    ws4b.append([
        gettext("Concepto"),
        gettext("Categoría"),
        gettext("Fecha"),
        gettext("Monto"),
    ])
    style_header(ws4b, 1, 4)
    for e in m["extraordinarios_detalle"]:
        ws4b.append(
            [e["concepto"], str(e["categoria"]), e["fecha"].strftime("%d/%m/%Y"), float(e["monto"])]
        )
    autosize(ws4b)

    # --- Hoja 6: Topes ---
    ws5 = wb.create_sheet(gettext("Topes"))
    ws5.append([
        gettext("Categoría"),
        gettext("Gasto"),
        gettext("Tope"),
        gettext("% usado"),
        gettext("¿Excedido?"),
    ])
    style_header(ws5, 1, 5)
    for t in m["topes_rows"]:
        ws5.append(
            [
                str(t["label"]),
                float(t["gasto"]),
                float(t["tope"]),
                float(t["pct"]),
                gettext("Sí") if t["excedido"] else gettext("No"),
            ]
        )
    autosize(ws5)

    buffer = BytesIO()
    wb.save(buffer)
    suffix = f"{m['year']}" + (f"_{m['month']:02d}" if m["month"] else "_anual")
    fname = f"gastos_3darg_{suffix}_{timezone.localdate().isoformat()}.xlsx"
    return fname, buffer.getvalue()
