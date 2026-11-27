"""«Deshacer» de Roque (27/11/2026, decisión del dueño).

Lo que pide Lucas se hace al toque y le llega un botón «Deshacer» por 10 minutos. Este endpoint revierte UNA
escritura de Roque (identificada por su `clave` de idempotencia, que queda en RegistroAgente), dentro de los
15 minutos y una sola vez:

    POST escritura/deshacer/   {clave, aprobo, clave_original}

  crear        → cancela el presupuesto (si sigue en Borrador/Enviado y sin pagos)
  aprobar      → vuelve a Borrador/Enviado devolviendo stock y sacando la cola (si no se empezó a imprimir)
  pago         → borra el pago y su movimiento de caja
  compra       → resta el stock comprado, vuelve los precios de antes y borra el egreso de caja
  gasto        → borra el gasto y su egreso de caja
  post_impresion / completar → vuelve al estado anterior
  envio / etapa_diseno / prioridad → vuelven los datos de antes
  prototipo    → se borra (el filamento vuelve al stock)
Lo demás (editar un pedido, vencer, costeo, alta de producto) no se deshace por acá.
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from .api_agentes_escritura import _VistaEscritura
from .models import Cliente, PagoPresupuesto, Presupuesto, Producto, RegistroAgente

PLAZO = timedelta(minutes=15)


class NoSePuede(Exception):
    pass


def _crear(reg):
    p = reg.presupuesto
    if not p:
        raise NoSePuede("no encuentro el presupuesto")
    if p.status not in (Presupuesto.Status.DRAFT, Presupuesto.Status.SENT):
        raise NoSePuede(f"ya está {p.get_status_display().lower()}: deshacé primero la aprobación")
    if p.pagos.exists():
        raise NoSePuede("tiene pagos cargados: deshacé primero el pago")
    p.cancel()
    return f"presupuesto #{p.pk} cancelado"


def _aprobar(reg):
    p = reg.presupuesto
    try:
        devuelto = p.desaprobar((reg.resultado.get("_antes") or {}).get("status"))
    except ValueError as exc:
        raise NoSePuede(str(exc))
    p.refresh_from_db()
    return f"presupuesto #{p.pk} vuelve a {p.get_status_display()} · {devuelto['jobs_cancelled']} trabajo(s) fuera de la cola"


def _pago(reg):
    from caja.models import MovimientoCaja

    pago = PagoPresupuesto.objects.filter(pk=(reg.resultado.get("_antes") or {}).get("pago_id")).first()
    if not pago:
        raise NoSePuede("ese pago ya no existe")
    p = pago.presupuesto
    mov = pago.movimiento_caja
    if mov:  # lo cargó Roque hace minutos: se anula sin contra-asiento
        MovimientoCaja.objects.filter(pk=mov.pk).update(estado=MovimientoCaja.Estado.DESCARTADO)
        pago.movimiento_caja = None
    pago.delete()
    p.refresh_from_db()
    return f"pago borrado del presupuesto #{p.pk} (cobrado ahora {p.total_cobrado})"


def _compra(reg):
    from caja.models import MovimientoCaja
    from inventory.models import Compra, StockMovement

    c = Compra.objects.filter(pk=reg.resultado.get("compra_id")).first()
    if not c:
        raise NoSePuede("esa compra ya no existe")
    for m in StockMovement.objects.filter(related_compra=c).select_related("filament", "aggregate"):
        art = m.filament or m.aggregate
        campo_stock = "stock_grams" if m.filament_id else "stock_quantity"
        campo_precio = "cost_per_kg" if m.filament_id else "cost_per_unit"
        setattr(art, campo_stock, (getattr(art, campo_stock) - m.quantity).quantize(Decimal("0.01")))
        precio = re.search(r"\$([\d.]+)/(?:kg|u) →", m.note or "")
        if precio:
            setattr(art, campo_precio, Decimal(precio.group(1)))
        art.save(update_fields=[campo_stock, campo_precio, "updated_at"])
        m.delete()
    MovimientoCaja.objects.filter(compra=c).delete()
    c.lines.all().delete()
    c.delete()
    return f"compra #{reg.resultado.get('compra_id')} borrada (stock, precios y caja como antes)"


def _gasto(reg):
    from caja.models import MovimientoCaja
    from gastos.models import Gasto

    g = Gasto.objects.filter(pk=reg.resultado.get("gasto_id")).first()
    if not g:
        raise NoSePuede("ese gasto ya no existe")
    MovimientoCaja.objects.filter(gasto=g).delete()
    g.delete()
    return f"gasto #{reg.resultado.get('gasto_id')} borrado (y su egreso de caja)"


def _volver_estado(reg, desde, hacia):
    p = reg.presupuesto
    if p.status != desde:
        raise NoSePuede(f"ya no está {Presupuesto.Status(desde).label.lower()} (está {p.get_status_display().lower()})")
    if desde == Presupuesto.Status.COMPLETED and p.para_stock:
        raise NoSePuede("era para stock y ya sumó sus terminados")
    p.status = hacia
    campos = ["status", "updated_at"]
    if hacia == Presupuesto.Status.POST_PRINT:
        p.ready_at = None
        campos.append("ready_at")
    if desde == Presupuesto.Status.COMPLETED:
        p.completed_at, p.delivered_by = None, ""
        campos += ["completed_at", "delivered_by"]
    p.save(update_fields=campos)
    return f"presupuesto #{p.pk} vuelve a {p.get_status_display()}"


def _envio(reg):
    p, antes = reg.presupuesto, reg.resultado.get("_antes")
    if not antes:
        raise NoSePuede("no guardé cómo estaba antes")
    p.forma_entrega = antes["forma_entrega"]
    p.costo_envio = Decimal(antes["costo_envio"] or "0")
    p.envio_cobrado_al_cliente = antes["envio_cobrado_al_cliente"]
    p.direccion_envio, p.seguimiento_envio = antes["direccion_envio"], antes["seguimiento_envio"]
    p.save()
    p.recalcular_cobro()
    return f"envío del presupuesto #{p.pk} como antes"


def _etapa(reg):
    antes = reg.resultado.get("_antes")
    prod = Producto.objects.filter(pk=reg.resultado.get("producto_id")).first()
    if not antes or not prod:
        raise NoSePuede("no guardé cómo estaba antes")
    if prod.estado_diseno == Producto.EstadoDiseno.LISTO and antes["etapa"] != Producto.EstadoDiseno.LISTO:
        raise NoSePuede("«Diseño listo» ya mandó las líneas a la cola: cambiá la etapa de nuevo si hace falta")
    Producto.objects.filter(pk=prod.pk).update(estado_diseno=antes["etapa"], rondas_cliente=antes["rondas_cliente"],
                                               diseno_listo=antes["etapa"] == Producto.EstadoDiseno.LISTO)
    return f"«{prod.name}» vuelve a la etapa anterior"


def _prioridad(reg):
    antes = reg.resultado.get("_antes")
    if not antes:
        raise NoSePuede("no guardé cómo estaba antes")
    c = Cliente.para_nombre(reg.resultado.get("cliente"))
    c.prioridad = antes["prioridad"]
    c.save()
    return f"{c.nombre}: prioridad como antes"


def _prototipo(reg):
    from production.models import Prototipo

    proto = Prototipo.objects.filter(pk=reg.resultado.get("prototipo_id")).first()
    if not proto:
        raise NoSePuede("ese prototipo ya no existe")
    proto.delete()  # la señal devuelve el filamento
    return "prototipo borrado (el filamento volvió al stock)"


DESHACER = {
    "crear": _crear, "aprobar": _aprobar, "pago": _pago, "compra": _compra, "gasto": _gasto,
    "post_impresion": lambda r: _volver_estado(r, Presupuesto.Status.READY, Presupuesto.Status.POST_PRINT),
    "completar": lambda r: _volver_estado(r, Presupuesto.Status.COMPLETED, Presupuesto.Status.READY),
    "envio": _envio, "etapa_diseno": _etapa, "prioridad": _prioridad, "prototipo": _prototipo,
}


class DeshacerEscritura(_VistaEscritura):
    def post(self, request):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        reg = RegistroAgente.objects.filter(clave=str(request.data.get("clave_original") or "")).first()
        if not reg:
            return Response({"error": "No encuentro esa operación."}, status=404)
        if reg.resultado.get("deshecho"):
            return Response({"error": "Esa operación ya se deshizo."}, status=409)
        if timezone.now() - reg.creado > PLAZO:
            return Response({"error": "Pasaron más de 15 minutos: ya no se puede deshacer desde acá."}, status=409)
        funcion = DESHACER.get(reg.operacion)
        if not funcion:
            return Response({"error": f"«{reg.operacion}» no se deshace desde acá."}, status=409)
        try:
            with transaction.atomic():
                texto = funcion(reg)
                reg.resultado = {**reg.resultado, "deshecho": timezone.now().isoformat(), "deshizo": aprobo}
                reg.save(update_fields=["resultado"])
                resultado = {"deshecho": True, "operacion": reg.operacion, "detalle": texto, "deshizo": reg.operacion}
                self._registrar("deshacer", clave, aprobo, reg.presupuesto, request.data, resultado)
        except NoSePuede as exc:
            return Response({"error": f"No se puede deshacer: {exc}."}, status=409)
        return Response(resultado)
