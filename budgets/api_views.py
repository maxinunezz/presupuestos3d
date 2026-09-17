"""
Endpoint de solo lectura para automatizar el Reporte Ejecutivo Mensual (RH-003).

Pensado para que "Schedule by Zapier" lo consulte el día 1 de cada mes (o el
día que se elija) y arme el Reporte Ejecutivo Mensual sin que nadie tenga que
completarlo a mano: los KPIs de la Sección B salen de los datos reales del
sistema. "Objetivo/Plan" queda afuera a propósito, porque no hay metas
cargadas en ningún lado — eso lo define el socio de dirección en la reunión,
no un dato del sistema.
"""

from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .metrics import _money, _pct, build_metrics

_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _target_month_anchor(mes_param: str | None) -> datetime:
    """
    Devuelve un datetime dentro del mes a reportar.

    Sin `mes`, apunta al mes calendario anterior al actual (el que acaba de
    cerrar). Con `mes=YYYY-MM`, apunta a ese mes puntual (para generar un
    reporte de un período pasado a demanda).
    """
    if mes_param:
        year, month = (int(x) for x in mes_param.split("-"))
        return timezone.make_aware(datetime(year, month, 15))
    now_local = timezone.localtime(timezone.now())
    first_of_this_month = now_local.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return first_of_this_month - timedelta(days=1)


class ReporteEjecutivoMensualView(APIView):
    """
    GET /api/reportes/ejecutivo-mensual/?mes=YYYY-MM

    Devuelve la Sección B (Tablero de Control Operativo y Financiero) del
    Reporte Ejecutivo Mensual con los valores "Real Obtenido" calculados.
    """

    def get(self, request):
        target = _target_month_anchor(request.query_params.get("mes"))
        m = build_metrics("month", now=target)

        cur_start_local = timezone.localtime(m["cur_start"])
        data = {
            "periodo": f"{_MESES_ES[cur_start_local.month - 1]} {cur_start_local.year}",
            "kpis": {
                "facturacion_total_neta": _money(m["facturacion"]),
                "costos_operativos_directos": _money(m["consumo_valor"]),
                "unidades_fabricadas": m["piezas_impresas"],
                "cumplimiento_entregas_pct": _pct(m["cumplimiento"]),
                "indice_retrabajos_pct": _pct(m["reprint_rate"]),
            },
            # No forma parte de la plantilla RH-003, pero da contexto útil
            # para la charla del directorio.
            "contexto_adicional": {
                "presupuestos_aprobados": m["n_aprobados"],
                "ticket_promedio": _money(m["ticket"]),
                "horas_impresion": str(m["horas_impresas"]),
                "compras_confirmadas": m["n_compras"],
                "gasto_en_compras": _money(m["gasto_compras"]),
                "items_bajo_stock": m["low_stock"],
                "margen_pct": _pct(m["margen_pct"]),
            },
        }
        return Response(data)
