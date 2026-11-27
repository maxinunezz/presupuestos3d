"""Escritura para Roque del circuito venta → diseño → producción → entrega (27/11/2026).

Mismas reglas que api_agentes_escritura.py: token de escritura propio, `clave` de idempotencia, `aprobo` (quién lo
aprobó en Telegram) y todo queda en RegistroAgente. Roque pide la aprobación del lado de Telegram antes de llamar.

- POST escritura/presupuestos/<id>/pago/            {tipo: SENA|SALDO|DEVOLUCION, monto, medio, fecha?, notas?}
- POST escritura/presupuestos/<id>/post-impresion/  terminar post-impresión → Listo para entregar
- POST escritura/presupuestos/<id>/envio/           {forma, costo?, a_cargo_cliente?, direccion?, seguimiento?}
- POST escritura/presupuestos/<id>/editar/          {items: [{producto_id, cantidad, precio?}]} → recalcula todo
- POST escritura/presupuestos/vencer/               cancela los Enviados con más de 10 días (y avisa)
- POST escritura/productos/<id>/etapa-diseno/       {etapa: EN_DISENO|ESPERANDO_CLIENTE|LISTO}
- POST escritura/clientes/prioridad/                {cliente, prioridad: 1|2|3|9}
- POST escritura/prototipos/                        {descripcion, producto_id?, presupuesto_id?, filamentos: [...]}

Diseño listo: lo decide solo Lucas. Roque lo impone con el rol (solo el CEO puede pedirlo) y acá se exige además que
`aprobo` sea alguien de DISENO_LISTO_APRUEBAN (por defecto «Lucas»).
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from rest_framework.response import Response

from .api_agentes import _dinero
from .api_agentes_escritura import _estado, _presupuesto, _VistaEscritura
from .models import Cliente, PagoPresupuesto, Presupuesto, PresupuestoItem, Producto


def _dec(valor):
    """Número o texto: «20000», «20.000,50» (formato argentino), «20000.5», «$ 20.000»."""
    t = str(valor if valor is not None else "").replace("$", "").replace(" ", "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[1]) == 3):
        t = t.replace(".", "")  # «20.000» = veinte mil
    try:
        return Decimal(t)
    except (InvalidOperation, ValueError):
        return None


def _cobro(p):
    return {**_estado(p), "estado_venta": p.get_estado_venta_display(), "cobrado": _dinero(p.total_cobrado),
            "saldo": _dinero(p.saldo), "sena_faltante": _dinero(p.sena_faltante())}


class PagoEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _presupuesto(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe."}, status=404)
        d = request.data
        tipo = str(d.get("tipo") or "SENA").upper()
        medio = str(d.get("medio") or "").upper().replace(" ", "").replace("Á", "A")
        monto = _dec(d.get("monto"))
        if tipo not in PagoPresupuesto.Tipo.values:
            return Response({"error": f"tipo inválido ({', '.join(PagoPresupuesto.Tipo.values)})."}, status=400)
        if medio not in Presupuesto.MedioPago.values:
            return Response({"error": f"medio inválido ({', '.join(Presupuesto.MedioPago.values)})."}, status=400)
        if monto is None or monto <= 0:
            return Response({"error": "monto inválido."}, status=400)
        try:
            fecha = date.fromisoformat(str(d["fecha"])) if d.get("fecha") else None
        except ValueError:
            return Response({"error": "fecha inválida (YYYY-MM-DD)."}, status=400)
        with transaction.atomic():
            PagoPresupuesto.objects.create(presupuesto=p, tipo=tipo, monto=monto, medio_pago=medio,
                                           notas=str(d.get("notas") or "")[:200], cargado_por=f"Roque · {aprobo}"[:60],
                                           **({"fecha": fecha} if fecha else {}))
            p.refresh_from_db()
            resultado = _cobro(p)
            self._registrar("pago", clave, aprobo, p, d, resultado)
        return Response(resultado, status=201)


class PostImpresionEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _presupuesto(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe."}, status=404)
        if not p.terminar_post_impresion():
            return Response({"error": f"No está en post-impresión (está {p.get_status_display().lower()})."}, status=409)
        p.refresh_from_db()
        resultado = _estado(p)
        self._registrar("post_impresion", clave, aprobo, p, request.data, resultado)
        return Response(resultado)


class EnvioEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _presupuesto(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe."}, status=404)
        d = request.data
        forma = str(d.get("forma") or "").upper()
        if forma not in Presupuesto.FormaEntrega.values:
            return Response({"error": f"forma inválida ({', '.join(Presupuesto.FormaEntrega.values)})."}, status=400)
        p.forma_entrega = forma
        if d.get("costo") not in (None, ""):
            costo = _dec(d.get("costo"))
            if costo is None or costo < 0:
                return Response({"error": "costo inválido."}, status=400)
            p.costo_envio = costo
        if "a_cargo_cliente" in d:
            p.envio_cobrado_al_cliente = bool(d.get("a_cargo_cliente"))
        if d.get("direccion"):
            p.direccion_envio = str(d["direccion"])[:250]
        if d.get("seguimiento"):
            p.seguimiento_envio = str(d["seguimiento"])[:120]
        p.save()
        p.recalcular_cobro()
        resultado = _cobro(p)
        self._registrar("envio", clave, aprobo, p, d, resultado)
        return Response(resultado)


class EditarPresupuestoEscritura(_VistaEscritura):
    """Reemplaza las líneas de un pedido. En Borrador/Enviado solo cambia las líneas; si ya está confirmado
    recalcula todo con recalcular_pedido() (devuelve lo no arrancado, conserva lo impreso, rearma la cola)."""

    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _presupuesto(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe."}, status=404)
        if p.status in (Presupuesto.Status.COMPLETED, Presupuesto.Status.CANCELLED):
            return Response({"error": f"No se puede editar: está {p.get_status_display().lower()}."}, status=409)
        try:
            lineas = [(int(i["producto_id"]), int(i["cantidad"]), _dec(i["precio"]) if i.get("precio") not in (None, "")
                       else None) for i in (request.data.get("items") or [])]
        except (KeyError, ValueError, TypeError):
            return Response({"error": "Ítems inválidos: [{producto_id, cantidad, precio?}]"}, status=400)
        if not lineas or any(c < 0 for _, c, _ in lineas):
            return Response({"error": "Faltan ítems o hay una cantidad inválida."}, status=400)
        productos = {x.id: x for x in Producto.objects.filter(pk__in=[x[0] for x in lineas], is_active=True)}
        if len(productos) != len({x[0] for x in lineas}):
            return Response({"error": "Algún producto no existe o no está activo."}, status=400)
        confirmado = p.status in Presupuesto.ESTADOS_EDITABLES and p.stock_provisioned
        with transaction.atomic():
            antes = p.foto_items() if confirmado else None
            actuales = {i.producto_id: i for i in p.items.all()}
            for pid, cantidad, precio in lineas:
                item = actuales.pop(pid, None)
                if cantidad == 0:
                    if item:
                        item.delete()
                    continue
                if item:
                    item.quantity = cantidad
                    if precio is not None:
                        item.unit_price, item.precio_provisorio = precio, False
                    item.save()
                else:
                    PresupuestoItem.objects.create(presupuesto=p, producto=productos[pid], quantity=cantidad,
                                                   unit_price=precio)
            for item in actuales.values():  # lo que no vino en la lista nueva se saca
                item.delete()
            r = p.recalcular_pedido(antes) if confirmado else None
            p.refresh_from_db()
            resultado = {**_cobro(p), "recalculado": bool(r),
                         "faltantes": [{"item": str(x.get("item")), "falta": str(x.get("missing"))}
                                       for x in (r or {}).get("shortages", [])]}
            self._registrar("editar", clave, aprobo, p, request.data, resultado)
        return Response(resultado)


class VencerPresupuestosEscritura(_VistaEscritura):
    def post(self, request):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        vencidos = Presupuesto.vencer_sin_respuesta(simular=bool(request.data.get("simular")))
        resultado = {"vencidos": vencidos, "simulado": bool(request.data.get("simular"))}
        if not request.data.get("simular"):
            self._registrar("vencer", clave, aprobo, None, request.data, resultado)
        return Response(resultado)


class EtapaDisenoEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        prod = Producto.objects.filter(pk=pk).first()
        if not prod:
            return Response({"error": "Ese producto no existe."}, status=404)
        etapa = str(request.data.get("etapa") or "").upper()
        if etapa not in Producto.EstadoDiseno.values:
            return Response({"error": f"etapa inválida ({', '.join(Producto.EstadoDiseno.values)})."}, status=400)
        toca_listo = (prod.estado_diseno == Producto.EstadoDiseno.LISTO) != (etapa == Producto.EstadoDiseno.LISTO)
        habilitados = [x.lower() for x in getattr(settings, "DISENO_LISTO_APRUEBAN", ["Lucas"])]
        if toca_listo and aprobo.lower() not in habilitados:
            return Response({"error": "Diseño listo lo marca solo Lucas (cierra diseño y cotización)."}, status=403)
        prod.estado_diseno = etapa
        prod.save()
        prod.refresh_from_db()
        resultado = {"producto_id": prod.id, "nombre": prod.name, "etapa": prod.get_estado_diseno_display(),
                     "rondas_cliente": prod.rondas_cliente, "cobra_rediseno": prod.cobra_rediseno,
                     "precio": _dinero(prod.sale_price)}
        self._registrar("etapa_diseno", clave, aprobo, None, request.data, resultado)
        return Response(resultado)


class PrioridadClienteEscritura(_VistaEscritura):
    def post(self, request):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        nombre = str(request.data.get("cliente") or "").strip()
        try:
            prioridad = int(request.data.get("prioridad"))
        except (TypeError, ValueError):
            prioridad = None
        if not nombre or prioridad not in Producto.Priority.values:
            return Response({"error": "Faltan 'cliente' y 'prioridad' (1 Alta, 2 Media, 3 Baja, 9 Sin)."}, status=400)
        c = Cliente.para_nombre(nombre)
        c.prioridad = prioridad
        c.save()
        resultado = {"cliente": c.nombre, "prioridad": c.get_prioridad_display()}
        self._registrar("prioridad", clave, aprobo, None, request.data, resultado)
        return Response(resultado)


class PrototipoEscritura(_VistaEscritura):
    def post(self, request):
        from inventory.models import Filament
        from production.models import Prototipo, PrototipoFilamento

        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        d = request.data
        if not d.get("descripcion") or not d.get("filamentos"):
            return Response({"error": "Faltan 'descripcion' y 'filamentos' [{filament_id, gramos}]."}, status=400)
        try:
            lineas = [(int(x["filament_id"]), _dec(x["gramos"])) for x in d["filamentos"]]
        except (KeyError, ValueError, TypeError):
            return Response({"error": "filamentos inválidos: [{filament_id, gramos}]."}, status=400)
        filamentos = {f.id: f for f in Filament.objects.filter(pk__in=[x[0] for x in lineas])}
        if len(filamentos) != len({x[0] for x in lineas}) or any(g is None or g <= 0 for _, g in lineas):
            return Response({"error": "Algún filamento no existe o los gramos son inválidos."}, status=400)
        with transaction.atomic():
            proto = Prototipo.objects.create(
                descripcion=str(d["descripcion"])[:200], hecho_por=str(d.get("hecho_por") or aprobo)[:60],
                producto=Producto.objects.filter(pk=d.get("producto_id")).first() if d.get("producto_id") else None,
                presupuesto=Presupuesto.objects.filter(pk=d.get("presupuesto_id")).first()
                if d.get("presupuesto_id") else None,
                notas=str(d.get("notas") or "")[:2000])
            for fid, gramos in lineas:
                PrototipoFilamento.objects.create(prototipo=proto, filament=filamentos[fid], gramos=gramos)
            resultado = {"prototipo_id": proto.id, "descripcion": proto.descripcion,
                         "filamentos": [{"nombre": str(filamentos[f]), "gramos": str(g),
                                         "stock_hoy_g": str(Filament.objects.get(pk=f).stock_grams)} for f, g in lineas]}
            self._registrar("prototipo", clave, aprobo, proto.presupuesto, d, resultado)
        return Response(resultado, status=201)
