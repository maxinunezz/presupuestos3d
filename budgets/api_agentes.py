"""
API de solo lectura para los agentes de IA (Roque y su equipo), bajo /api/agentes/.

- Autenticación propia: header `X-Agentes-Token` contra la env var `AGENTES_API_TOKEN`
  (comparación en tiempo constante). Sin la env var configurada, todo responde 403
  (falla cerrado). Es un token distinto del de Zapier / 3darg-backend: no sirve para
  ningún otro endpoint de la API.
- Solo GET: el permiso rechaza cualquier otro método aunque el token sea válido, y
  ninguna vista escribe en la base.
- No expone datos de contacto de clientes (solo el nombre que figura en el presupuesto).
"""

import hmac
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from caja.models import CuentaCaja, MovimientoCaja
from gastos.models import Gasto, TopeGasto
from inventory.models import Aggregate, Filament
from production.models import Maquina, ProductionJob

from .api_views import ReporteEjecutivoMensualView
from .models import Presupuesto


class TokenAgentes(BasePermission):
    message = "Token de agentes inválido o ausente."

    def has_permission(self, request, view):
        esperado = getattr(settings, "AGENTES_API_TOKEN", "") or ""
        recibido = request.headers.get("X-Agentes-Token", "")
        return (
            request.method in ("GET", "HEAD")
            and bool(esperado)
            and hmac.compare_digest(recibido, esperado)
        )


class _VistaAgentes(APIView):
    authentication_classes = []  # no usa usuarios de Django: solo el token de agentes
    permission_classes = [TokenAgentes]


def _dinero(x) -> str:
    return str((x or Decimal("0")).quantize(Decimal("0.01")))


def _rango(request, dias_default=30):
    """?desde=YYYY-MM-DD&hasta=YYYY-MM-DD (por defecto, los últimos 30 días)."""
    hoy = timezone.localdate()
    try:
        hasta = date.fromisoformat(request.query_params.get("hasta") or hoy.isoformat())
        desde = date.fromisoformat(
            request.query_params.get("desde") or (hasta - timedelta(days=dias_default)).isoformat()
        )
    except ValueError:
        hasta, desde = hoy, hoy - timedelta(days=dias_default)
    return desde, hasta


class StockAgentes(_VistaAgentes):
    """GET /api/agentes/stock/ — filamentos e insumos activos, con alerta de stock bajo."""

    def get(self, request):
        filamentos = [
            {
                "id": f.id, "marca": f.brand, "material": f.material_type, "color": f.color,
                "stock_g": str(f.stock_grams), "minimo_g": str(f.min_stock),
                "bajo": f.is_low_stock, "costo_kg": _dinero(f.cost_per_kg),
            }
            for f in Filament.objects.filter(is_active=True).order_by("material_type", "color")
        ]
        insumos = [
            {
                "id": a.id, "nombre": a.name, "categoria": str(a.category or ""), "unidad": a.unit,
                "stock": str(a.stock_quantity), "minimo": str(a.min_stock), "bajo": a.is_low_stock,
                "costo_unidad": _dinero(a.cost_per_unit),
            }
            for a in Aggregate.objects.filter(is_active=True).select_related("category").order_by("name")
        ]
        return Response({
            "filamentos": filamentos, "insumos": insumos,
            "bajo_stock": [x for x in filamentos + insumos if x["bajo"]],
        })


class ProduccionAgentes(_VistaAgentes):
    """GET /api/agentes/produccion/ — cola de impresión (en cola + imprimiendo) y máquinas."""

    def get(self, request):
        abiertos = (
            ProductionJob.objects
            .filter(status__in=[ProductionJob.Status.PENDING, ProductionJob.Status.PRINTING])
            .select_related("machine", "producto", "pieza", "presupuesto")
            .order_by("machine__name", "order")
        )
        cola = [
            {
                "id": j.id, "estado": j.get_status_display(), "maquina": j.machine.name if j.machine else None,
                "producto": j.producto.name if j.producto else None,
                "pieza": j.pieza.name if j.pieza else None, "cantidad": j.quantity,
                "presupuesto": j.presupuesto_id, "cliente": j.presupuesto.client_name if j.presupuesto else None,
                "inicio_estimado": j.estimated_start, "fin_estimado": j.estimated_print_end,
                "iniciado": j.started_at,
            }
            for j in abiertos
        ]
        maquinas = [
            {"nombre": m.name, "multicolor": m.supports_multicolor, "horas_totales": str(m.total_hours_printed),
             "trabajos_en_cola": sum(1 for j in cola if j["maquina"] == m.name)}
            for m in Maquina.objects.filter(is_active=True).order_by("name")
        ]
        return Response({"cola": cola, "maquinas": maquinas})


class PresupuestosAgentes(_VistaAgentes):
    """GET /api/agentes/presupuestos/?estado=APPROVED&desde=YYYY-MM-DD — presupuestos con su total y cobro."""

    def get(self, request):
        desde, hasta = _rango(request, dias_default=90)
        qs = Presupuesto.objects.filter(created_at__date__gte=desde, created_at__date__lte=hasta)
        if request.query_params.get("estado"):
            qs = qs.filter(status=request.query_params["estado"].upper())
        if request.query_params.get("estado_venta"):
            qs = qs.filter(estado_venta=request.query_params["estado_venta"].upper())
        datos = [
            {
                "id": p.id, "cliente": p.client_name, "estado": p.get_status_display(),
                "estado_venta": p.get_estado_venta_display(), "medio_pago": p.medio_pago or None,
                "fecha_cobro": p.fecha_cobro, "vencimiento": p.due_date, "para_stock": p.para_stock,
                "total": _dinero(p.total), "ganancia": _dinero(p.profit_total),
                "creado": p.created_at, "aprobado": p.approved_at, "completado": p.completed_at,
            }
            for p in qs.order_by("-created_at")[:200]
        ]
        return Response({"desde": desde, "hasta": hasta, "presupuestos": datos})


class CajaAgentes(_VistaAgentes):
    """GET /api/agentes/caja/?desde&hasta — saldo de cada cuenta y movimientos del período."""

    def get(self, request):
        desde, hasta = _rango(request)
        cuentas = [{"nombre": c.nombre, "saldo": _dinero(c.saldo_actual)}
                   for c in CuentaCaja.objects.filter(is_active=True).order_by("order", "nombre")]
        movs = (MovimientoCaja.objects.filter(fecha__gte=desde, fecha__lte=hasta)
                .exclude(estado=MovimientoCaja.Estado.DESCARTADO).select_related("cuenta").order_by("-fecha"))
        confirmados = movs.filter(estado=MovimientoCaja.Estado.CONFIRMADO)
        ingresos = confirmados.filter(tipo=MovimientoCaja.Tipo.INGRESO).aggregate(s=Sum("monto"))["s"]
        egresos = confirmados.filter(tipo=MovimientoCaja.Tipo.EGRESO).aggregate(s=Sum("monto"))["s"]
        return Response({
            "desde": desde, "hasta": hasta, "cuentas": cuentas,
            "ingresos_confirmados": _dinero(ingresos), "egresos_confirmados": _dinero(egresos),
            "movimientos": [
                {"fecha": m.fecha, "cuenta": m.cuenta.nombre, "tipo": m.tipo, "monto": _dinero(m.monto),
                 "concepto": m.concepto, "estado": m.estado, "presupuesto": m.presupuesto_id}
                for m in movs[:300]
            ],
        })


class GastosAgentes(_VistaAgentes):
    """GET /api/agentes/gastos/?desde&hasta — gastos del período, total por categoría y topes del mes."""

    def get(self, request):
        desde, hasta = _rango(request)
        gastos = (Gasto.objects.filter(fecha__gte=desde, fecha__lte=hasta)
                  .select_related("categoria", "area", "medio_pago").order_by("-fecha"))
        por_categoria = {}
        for g in gastos:
            clave = str(g.categoria or "Sin categoría")
            por_categoria[clave] = por_categoria.get(clave, Decimal("0")) + g.monto
        hoy = timezone.localdate()
        inicio_mes = hoy.replace(day=1)
        topes = []
        for t in TopeGasto.objects.select_related("categoria"):
            gastado = (Gasto.objects.filter(categoria=t.categoria, fecha__gte=inicio_mes, fecha__lte=hoy)
                       .aggregate(s=Sum("monto"))["s"])
            topes.append({"categoria": str(t.categoria), "tope_mensual": _dinero(t.monto_mensual),
                          "gastado_este_mes": _dinero(gastado),
                          "superado": (gastado or 0) > t.monto_mensual})
        return Response({
            "desde": desde, "hasta": hasta,
            "total": _dinero(sum((g.monto for g in gastos), Decimal("0"))),
            "por_categoria": {k: _dinero(v) for k, v in sorted(por_categoria.items())},
            "topes": topes,
            "gastos": [
                {"fecha": g.fecha, "concepto": g.concepto, "categoria": str(g.categoria or ""),
                 "area": str(g.area or ""), "monto": _dinero(g.monto), "proveedor": g.proveedor,
                 "medio_pago": str(g.medio_pago or ""), "recurrente": g.es_recurrente}
                for g in gastos[:300]
            ],
        })


class ReporteMensualAgentes(_VistaAgentes, ReporteEjecutivoMensualView):
    """GET /api/agentes/reporte-mensual/?mes=YYYY-MM — el mismo reporte ejecutivo, con el token de agentes."""
