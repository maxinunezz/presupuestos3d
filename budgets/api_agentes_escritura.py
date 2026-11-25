"""
Escritura para Roque (etapa 3) — SOLO 3 operaciones, cada una aprobada en Telegram por Lucas (o su suplente)
con doble pregunta:

- POST escritura/presupuestos/                 → crea un presupuesto en BORRADOR (no toca stock ni cola)
- POST escritura/presupuestos/<id>/aprobar/    → lo aprueba con Presupuesto.approve(): descuenta stock, arma la cola
- POST escritura/presupuestos/<id>/cancelar/   → lo cancela con Presupuesto.cancel(): devuelve stock, saca de la cola

Reglas:
- Token PROPIO (`X-Agentes-Escritura-Token` contra AGENTES_ESCRITURA_TOKEN): el de lectura no sirve acá y este no
  sirve para leer. Sin la variable configurada, todo 403 (falla cerrado). Solo POST.
- Cada pedido trae `clave` (idempotencia) y `aprobo` (quién lo aprobó en Telegram). Todo queda en RegistroAgente.
- Roque solo puede aprobar o cancelar presupuestos que creó él (los cargados a mano no se tocan).
- No toca precios de productos, stock directo, cobros ni caja.
"""

import hmac
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_agentes import _dinero
from .models import (
    DisenoNoListoError,
    Presupuesto,
    PresupuestoItem,
    PresupuestoNotApprovableError,
    Producto,
    RegistroAgente,
)


class TokenEscritura(BasePermission):
    message = "Token de escritura inválido o ausente."

    def has_permission(self, request, view):
        esperado = getattr(settings, "AGENTES_ESCRITURA_TOKEN", "") or ""
        recibido = request.headers.get("X-Agentes-Escritura-Token", "")
        return request.method == "POST" and bool(esperado) and hmac.compare_digest(recibido, esperado)


class _VistaEscritura(APIView):
    authentication_classes = []
    permission_classes = [TokenEscritura]

    def _base(self, request):
        clave = str(request.data.get("clave") or "").strip()
        aprobo = str(request.data.get("aprobo") or "").strip()
        if not clave or not aprobo:
            return None, None, Response({"error": "Faltan 'clave' y 'aprobo'."}, status=400)
        previo = RegistroAgente.objects.filter(clave=clave).first()
        if previo:  # ya se hizo: mismo resultado, nada nuevo
            return clave, aprobo, Response({**previo.resultado, "repetido": True})
        return clave, aprobo, None

    def _registrar(self, operacion, clave, aprobo, presupuesto, pedido, resultado):
        try:
            RegistroAgente.objects.create(operacion=operacion, clave=clave, aprobo=aprobo, presupuesto=presupuesto,
                                          pedido=pedido, resultado=resultado)
        except IntegrityError:  # dos pedidos iguales al mismo tiempo: gana el primero
            raise


def _estado(p):
    return {"id": p.id, "estado": p.get_status_display(), "status": p.status, "cliente": p.client_name,
            "vendedor": p.vendedor, "total": _dinero(p.total), "entrega": p.due_date.isoformat() if p.due_date else None}


class CrearPresupuestoEscritura(_VistaEscritura):
    def post(self, request):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        d = request.data
        items = d.get("items") or []
        if not d.get("cliente") or not items:
            return Response({"error": "Faltan 'cliente' e 'items'."}, status=400)
        try:
            lineas = [(int(i["producto_id"]), int(i["cantidad"]),
                       Decimal(str(i["precio"])) if i.get("precio") not in (None, "") else None) for i in items]
        except (KeyError, ValueError, TypeError, InvalidOperation):
            return Response({"error": "Ítems inválidos: [{producto_id, cantidad, precio}]"}, status=400)
        productos = {p.id: p for p in Producto.objects.filter(pk__in=[x[0] for x in lineas], is_active=True)}
        if len(productos) != len({x[0] for x in lineas}) or any(c <= 0 for _, c, _ in lineas):
            return Response({"error": "Algún producto no existe o la cantidad es inválida."}, status=400)
        with transaction.atomic():
            p = Presupuesto.objects.create(
                client_name=str(d["cliente"])[:150], vendedor=str(d.get("vendedor") or "")[:60],
                description=(str(d.get("notas") or "") + f"\n\nCargado por Roque · aprobó {aprobo}").strip(),
                status=Presupuesto.Status.DRAFT,
            )
            for pid, cantidad, precio in lineas:
                PresupuestoItem.objects.create(presupuesto=p, producto=productos[pid], quantity=cantidad,
                                               unit_price=precio if precio is not None else productos[pid].sale_price)
            resultado = _estado(p)
            self._registrar("crear", clave, aprobo, p, d, resultado)
        return Response(resultado, status=201)


def _de_roque(pk):
    """Solo los presupuestos que creó Roque."""
    return Presupuesto.objects.filter(pk=pk, registros_agente__operacion="crear").first()


class AprobarPresupuestoEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _de_roque(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe o no lo cargó Roque."}, status=404)
        try:
            r = p.approve()
        except (DisenoNoListoError, PresupuestoNotApprovableError) as exc:
            return Response({"error": str(exc), "motivo": type(exc).__name__}, status=409)
        p.refresh_from_db()
        trabajos = list(p.jobs.select_related("machine"))
        resultado = {**_estado(p), "faltantes": [{"item": str(x.get("item")), "falta": str(x.get("missing"))}
                                                 for x in r.get("shortages", [])],
                     "desde_stock": len(r.get("from_stock", [])) + len(r.get("from_finished", [])),
                     "trabajos": len(trabajos), "maquinas": sorted({j.machine.name for j in trabajos if j.machine})}
        self._registrar("aprobar", clave, aprobo, p, request.data, resultado)
        return Response(resultado)


class CancelarPresupuestoEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _de_roque(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe o no lo cargó Roque."}, status=404)
        if p.status in (Presupuesto.Status.COMPLETED, Presupuesto.Status.CANCELLED):
            return Response({"error": f"No se puede cancelar: está {p.get_status_display()}."}, status=409)
        r = p.cancel()
        p.refresh_from_db()
        resultado = {**_estado(p), "trabajos_cancelados": r.get("jobs_cancelled", 0)}
        self._registrar("cancelar", clave, aprobo, p, request.data, resultado)
        return Response(resultado)
