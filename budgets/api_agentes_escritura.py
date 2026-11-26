"""
Escritura para Roque (etapa 3) — SOLO 3 operaciones, cada una aprobada en Telegram por Lucas (o su suplente)
con doble pregunta:

- POST escritura/presupuestos/                 → crea un presupuesto en BORRADOR (no toca stock ni cola)
- POST escritura/presupuestos/<id>/aprobar/    → lo aprueba con Presupuesto.approve(): descuenta stock, arma la cola
- POST escritura/presupuestos/<id>/cancelar/   → lo cancela con Presupuesto.cancel(): devuelve stock, saca de la cola
- POST escritura/presupuestos/<id>/completar/  → lo marca entregado (misma regla que la acción del admin)
- POST escritura/compras/                      → compra de insumos CONFIRMADA: suma stock y actualiza el precio
- POST escritura/gastos/                       → un gasto (lo que no es insumo)
- POST escritura/productos/<id>/verificar-costeo/ → marca el costeo como revisado por Producción
- POST escritura/piezas/<id>/corregir-costeo/   → corrige piezas por impresión, minutos o gramos de una pieza

Reglas:
- Token PROPIO (`X-Agentes-Escritura-Token` contra AGENTES_ESCRITURA_TOKEN): el de lectura no sirve acá y este no
  sirve para leer. Sin la variable configurada, todo 403 (falla cerrado). Solo POST.
- Cada pedido trae `clave` (idempotencia) y `aprobo` (quién lo aprobó en Telegram). Todo queda en RegistroAgente.
- Puede operar sobre cualquier presupuesto, también los cargados a mano (cambio del 25/11/2026): la protección es
  la aprobación del CEO con doble pregunta en Telegram (o de IT como suplente a las 12 h), del lado de Roque.
- No toca precios de productos, stock directo ni cobros. En caja solo deja BORRADORES de egreso (los confirma el
  dueño).
"""

import hmac
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
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
    return {"id": p.id, "cargado_por_roque": p.registros_agente.filter(operacion="crear").exists(), "estado": p.get_status_display(), "status": p.status, "cliente": p.client_name,
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


def _presupuesto(pk):
    return Presupuesto.objects.filter(pk=pk).first()


class AprobarPresupuestoEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _presupuesto(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe."}, status=404)
        productos = [i.producto for i in p.items.select_related("producto") if i.producto]
        # si falta el diseño, manda ese error (es lo primero que hay que resolver)
        sin_verificar = [] if any(not x.diseno_listo for x in productos) else sorted(
            {x.name for x in productos if not x.costeo_verificado})
        if sin_verificar:  # Roque no descuenta stock con datos de producción que nadie revisó (26/11/2026)
            return Response({"error": "Costeo sin verificar: " + ", ".join(sin_verificar) + ".",
                             "motivo": "CosteoSinVerificar", "productos": sin_verificar}, status=409)
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
        p = _presupuesto(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe."}, status=404)
        if p.status in (Presupuesto.Status.COMPLETED, Presupuesto.Status.CANCELLED):
            return Response({"error": f"No se puede cancelar: está {p.get_status_display()}."}, status=409)
        r = p.cancel()
        p.refresh_from_db()
        resultado = {**_estado(p), "trabajos_cancelados": r.get("jobs_cancelled", 0)}
        self._registrar("cancelar", clave, aprobo, p, request.data, resultado)
        return Response(resultado)


class CompletarPresupuestoEscritura(_VistaEscritura):
    """Marcar entregado: igual que la acción del admin, solo si está listo para entregar (aprobado y sin trabajos
    de impresión pendientes). Si tiene impresiones abiertas, hay que marcarlas primero (no se deja la cola sucia)."""

    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = _presupuesto(pk)
        if not p:
            return Response({"error": "Ese presupuesto no existe."}, status=404)
        abiertos = p.jobs.exclude(status__in=["DONE", "CANCELLED"]).count()
        listo = p.is_ready_to_deliver or (p.status in (Presupuesto.Status.APPROVED, Presupuesto.Status.IN_PRODUCTION)
                                          and p.stock_provisioned and not p.para_stock and abiertos == 0)
        if not listo:
            motivo = (f"tiene {abiertos} trabajo(s) de impresión sin terminar: marcalos impresos en presupuestos3d primero"
                      if abiertos else f"está {p.get_status_display().lower()}")
            return Response({"error": f"No se puede marcar entregado: {motivo}."}, status=409)
        old = p.status
        p.status = Presupuesto.Status.COMPLETED
        p.apply_status_change(old)
        p.save()
        resultado = _estado(p)
        self._registrar("completar", clave, aprobo, p, request.data, resultado)
        return Response(resultado)


# ---------------------------------------------------------------- compras y gastos (26/11/2026)
# Lo que se compra va por uno de dos caminos, igual que en el admin:
#   - INSUMO (filamento o agregado que ya existe en Inventario) → una Compra confirmada: suma el stock, actualiza el
#     precio del artículo con lo pagado y registra el movimiento de stock (motivo Compra).
#   - GASTO (todo lo demás: contador, envío, herramienta, publicidad…) → un Gasto en el panel de gastos.
# En los dos, si se pagó con un medio de "caja inmediata" (efectivo, transferencia, Mercado Pago, Ualá) y hay una
# sola cuenta de caja para ese medio, queda un BORRADOR de egreso en caja para que el dueño lo confirme.

def _decimal(x):
    try:
        v = Decimal(str(x).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return v if v.is_finite() else None


def _cuenta_caja(medio_nombre):
    from caja.models import CuentaCaja
    from gastos.models import _NOMBRE_A_CODIGO_MEDIO_PAGO, _normalizar

    codigo = _NOMBRE_A_CODIGO_MEDIO_PAGO.get(_normalizar(medio_nombre or ""))
    if not codigo:
        return None
    cuentas = CuentaCaja.objects.filter(medio_pago_default=codigo, is_active=True)
    return cuentas.first() if cuentas.count() == 1 else None


class CrearCompraEscritura(_VistaEscritura):
    """POST escritura/compras/ {clave, aprobo, proveedor, factura, notas, medio_pago, fecha (YYYY-MM-DD),
    lineas: [{filamento_id | insumo_id | nuevo_insumo | nuevo_filamento, cantidad (g para filamento), precio_total}]}
    → Compra confirmada. nuevo_insumo {nombre, unidad (UNIT/PAIR/METER/GRAM), categoria, min_stock, notas} y
    nuevo_filamento {marca, material, color, min_stock} dan de alta el artículo en el mismo paso (27/11/2026)."""

    def post(self, request):
        from inventory.models import Aggregate, Compra, CompraLine, Filament

        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        d = request.data
        lineas = d.get("lineas") or []
        if not lineas:
            return Response({"error": "Faltan 'lineas'."}, status=400)
        from datetime import date, datetime, time

        from inventory.models import AggregateCategory

        try:
            fecha = date.fromisoformat(str(d["fecha"])) if d.get("fecha") else None
        except ValueError:
            return Response({"error": "Fecha inválida (YYYY-MM-DD)."}, status=400)
        armadas = []
        for li in lineas:
            cantidad = _decimal(li.get("cantidad"))
            precio = _decimal(li["precio_total"]) if li.get("precio_total") not in (None, "") else None
            fil_id, ins_id = li.get("filamento_id"), li.get("insumo_id")
            nuevo_i, nuevo_f = li.get("nuevo_insumo"), li.get("nuevo_filamento")
            if (not cantidad or cantidad <= 0 or (precio is not None and precio < 0)
                    or sum(bool(x) for x in (fil_id, ins_id, nuevo_i, nuevo_f)) != 1):
                return Response({"error": "Línea inválida: [{filamento_id | insumo_id | nuevo_insumo | nuevo_filamento, "
                                          "cantidad, precio_total}]"}, status=400)
            if nuevo_i:
                nombre = str(nuevo_i.get("nombre") or "").strip()[:150]
                unidad = str(nuevo_i.get("unidad") or "UNIT").upper()
                if not nombre or unidad not in Aggregate.Unit.values:
                    return Response({"error": f"Insumo nuevo inválido (nombre y unidad: {', '.join(Aggregate.Unit.values)})."},
                                    status=400)
                if Aggregate.objects.filter(name__iexact=nombre, is_active=True).exists():
                    return Response({"error": f"Ya existe el insumo «{nombre}»: usá su id."}, status=409)
                armadas.append((("insumo", nuevo_i, nombre, unidad), False, cantidad, precio))
                continue
            if nuevo_f:
                mat = str(nuevo_f.get("material") or "").upper()
                if mat not in Filament.MaterialType.values or not nuevo_f.get("marca") or not nuevo_f.get("color"):
                    return Response({"error": f"Filamento nuevo inválido (marca, color y material: "
                                              f"{', '.join(Filament.MaterialType.values)})."}, status=400)
                armadas.append((("filamento", nuevo_f, mat, None), True, cantidad, precio))
                continue
            item = (Filament.objects.filter(pk=fil_id, is_active=True).first() if fil_id
                    else Aggregate.objects.filter(pk=ins_id, is_active=True).first())
            if not item:
                return Response({"error": f"No existe el {'filamento' if fil_id else 'insumo'} {fil_id or ins_id}."},
                                status=400)
            armadas.append((item, bool(fil_id), cantidad, precio))
        with transaction.atomic():
            altas = []
            for i, (item, es_fil, cantidad, precio) in enumerate(armadas):
                if not isinstance(item, tuple):
                    continue
                tipo, datos_n, a, b = item
                unitario = (precio / (cantidad / 1000 if es_fil else cantidad)).quantize(Decimal("0.01")) if precio else Decimal("0")
                minimo = _decimal(datos_n.get("min_stock")) or Decimal("0")
                if tipo == "insumo":
                    cat = AggregateCategory.objects.filter(nombre__iexact=str(datos_n.get("categoria") or "").strip()).first()
                    nuevo = Aggregate.objects.create(name=a, unit=b, cost_per_unit=unitario, min_stock=minimo,
                                                     notes=str(datos_n.get("notas") or "Alta por Roque")[:500],
                                                     **({"category": cat} if cat else {}))
                else:
                    nuevo = Filament.objects.create(brand=str(datos_n["marca"])[:100], material_type=a,
                                                    color=str(datos_n["color"])[:100], cost_per_kg=unitario,
                                                    min_stock=minimo if minimo else Decimal("1000"))
                armadas[i] = (nuevo, es_fil, cantidad, precio)
                altas.append({"tipo": tipo, "id": nuevo.pk, "nombre": str(nuevo)})
            compra = Compra.objects.create(
                supplier=str(d.get("proveedor") or "")[:150], invoice_number=str(d.get("factura") or "")[:100],
                notes=(str(d.get("notas") or "") + f"\n\nCargada por Roque · aprobó {aprobo}").strip())
            antes = []
            for item, es_fil, cantidad, precio in armadas:
                antes.append((item, es_fil, item.stock_grams if es_fil else item.stock_quantity,
                              item.cost_per_kg if es_fil else item.cost_per_unit))
                CompraLine.objects.create(compra=compra, filament=item if es_fil else None,
                                          aggregate=None if es_fil else item, quantity=cantidad, unit_price=precio)
            if fecha:
                compra.confirmed_at = timezone.make_aware(datetime.combine(fecha, time(12)))
            compra.confirm()
            detalle = []
            for item, es_fil, stock_antes, precio_antes in antes:
                item.refresh_from_db()
                detalle.append({
                    "tipo": "filamento" if es_fil else "insumo", "id": item.id,
                    "nombre": f"{item.material_type} {item.color} ({item.brand})" if es_fil else item.name,
                    "stock_antes": str(stock_antes), "stock_despues": str(item.stock_grams if es_fil else item.stock_quantity),
                    "precio_antes": _dinero(precio_antes),
                    "precio_despues": _dinero(item.cost_per_kg if es_fil else item.cost_per_unit)})
            caja = None
            cuenta = _cuenta_caja(d.get("medio_pago"))
            if cuenta and compra.total > 0:
                from caja.models import MovimientoCaja

                MovimientoCaja.crear_borrador_automatico(
                    cuenta=cuenta, tipo=MovimientoCaja.Tipo.EGRESO, monto=compra.total,
                    fecha=fecha or timezone.localdate(), concepto=f"Compra #{compra.pk} — {compra.supplier or 'sin proveedor'}",
                    compra=compra)
                caja = cuenta.nombre
            resultado = {"compra_id": compra.pk, "estado": compra.get_status_display(), "total": _dinero(compra.total),
                         "lineas": detalle, "caja_borrador": caja, "altas": altas}
            self._registrar("compra", clave, aprobo, None, d, resultado)
        return Response(resultado, status=201)


class CrearGastoEscritura(_VistaEscritura):
    """POST escritura/gastos/ {clave, aprobo, concepto, monto, fecha, categoria, proveedor, medio_pago, notas,
    extraordinario} → Gasto. La categoría y el medio de pago tienen que existir (se buscan por nombre)."""

    def post(self, request):
        from datetime import date

        from gastos.models import CategoriaGasto, Gasto, MedioPagoGasto

        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        d = request.data
        monto = _decimal(d.get("monto"))
        concepto = str(d.get("concepto") or "").strip()
        if not concepto or not monto or monto <= 0:
            return Response({"error": "Faltan 'concepto' y 'monto' (mayor a 0)."}, status=400)
        categoria = CategoriaGasto.objects.filter(nombre__iexact=str(d.get("categoria") or "").strip()).first()
        if not categoria:
            return Response({"error": "Esa categoría de gasto no existe.",
                             "categorias": list(CategoriaGasto.objects.values_list("nombre", flat=True))}, status=400)
        medio = None
        if d.get("medio_pago"):
            from gastos.models import _NOMBRE_A_CODIGO_MEDIO_PAGO, _normalizar

            nombre_medio = str(d["medio_pago"]).strip()
            medio = next((m for m in MedioPagoGasto.objects.all() if _normalizar(m.nombre) == _normalizar(nombre_medio)), None)
            if not medio and _normalizar(nombre_medio) in _NOMBRE_A_CODIGO_MEDIO_PAGO:
                medio = MedioPagoGasto.objects.create(nombre=nombre_medio[:50])
        try:
            fecha = date.fromisoformat(str(d["fecha"])) if d.get("fecha") else timezone.localdate()
        except ValueError:
            return Response({"error": "Fecha inválida (YYYY-MM-DD)."}, status=400)
        with transaction.atomic():
            g = Gasto.objects.create(
                categoria=categoria, concepto=concepto[:150], monto=monto.quantize(Decimal("0.01")), fecha=fecha,
                proveedor=str(d.get("proveedor") or "")[:150], medio_pago=medio,
                tipo=Gasto.Tipo.EXTRAORDINARIO if d.get("extraordinario") else Gasto.Tipo.OPERATIVO,
                notas=(str(d.get("notas") or "") + f"\n\nCargado por Roque · aprobó {aprobo}").strip())
            caja = g.movimientos_caja.filter(origen_automatico=True).first() if hasattr(g, "movimientos_caja") else None
            resultado = {"gasto_id": g.pk, "concepto": g.concepto, "categoria": categoria.nombre,
                         "monto": _dinero(g.monto), "fecha": g.fecha.isoformat(),
                         "medio_pago": medio.nombre if medio else "", "caja_borrador": caja.cuenta.nombre if caja else None}
            self._registrar("gasto", clave, aprobo, None, d, resultado)
        return Response(resultado, status=201)



# ---------------------------------------------------------------- costeo verificado (26/11/2026)
# Antes de descontar stock con los datos de producción de un producto, alguien de Producción tiene que haber
# revisado que son reales. Verificar guarda la huella de los datos; si después cambian, se cae sola.

class VerificarCosteoEscritura(_VistaEscritura):
    def post(self, request, pk):
        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        p = Producto.objects.filter(pk=pk).first()
        if not p:
            return Response({"error": "Ese producto no existe."}, status=404)
        if not p.producto_piezas.exists():
            return Response({"error": "Ese producto no tiene piezas cargadas: no hay costeo para verificar."}, status=409)
        p.verificar_costeo(aprobo)
        resultado = {"producto_id": p.id, "nombre": p.name, "verificado": p.costeo_verificado,
                     "verificado_por": p.costeo_verificado_por}
        self._registrar("verificar", clave, aprobo, None, request.data, resultado)
        return Response(resultado)


class CorregirCosteoEscritura(_VistaEscritura):
    """{clave, aprobo, piezas_por_impresion?, minutos?, gramos?: {linea_id: g}}. No verifica: después de corregir,
    el producto se verifica aparte (con los datos nuevos a la vista)."""

    def post(self, request, pk):
        from .models import Pieza

        clave, aprobo, resp = self._base(request)
        if resp:
            return resp
        pieza = Pieza.objects.filter(pk=pk).first()
        if not pieza:
            return Response({"error": "Esa pieza no existe."}, status=404)
        d = request.data
        antes = {"piezas_por_impresion": pieza.pieces_per_gcode, "minutos": str(pieza.print_time_minutes),
                 "gramos": {str(fl.id): str(fl.grams_used) for fl in pieza.filament_lines.all()}}
        try:
            with transaction.atomic():
                if d.get("piezas_por_impresion") not in (None, ""):
                    n = int(d["piezas_por_impresion"])
                    if n < 1:
                        raise ValueError
                    pieza.pieces_per_gcode = n
                if d.get("minutos") not in (None, ""):
                    m = Decimal(str(d["minutos"]))
                    if m <= 0:
                        raise ValueError
                    pieza.print_time_minutes = m
                pieza.save(update_fields=["pieces_per_gcode", "print_time_minutes"])
                for linea_id, g in (d.get("gramos") or {}).items():
                    fl = pieza.filament_lines.filter(pk=int(linea_id)).first()
                    g = Decimal(str(g))
                    if not fl or g <= 0:
                        raise ValueError
                    fl.grams_used = g
                    fl.save(update_fields=["grams_used"])
                pieza.refresh_from_db()
                despues = {"piezas_por_impresion": pieza.pieces_per_gcode, "minutos": str(pieza.print_time_minutes),
                           "gramos": {str(fl.id): str(fl.grams_used) for fl in pieza.filament_lines.all()}}
                resultado = {"pieza_id": pieza.id, "nombre": pieza.name, "antes": antes, "despues": despues}
                self._registrar("corregir", clave, aprobo, None, d, resultado)
        except (ValueError, TypeError, InvalidOperation):
            return Response({"error": "Datos inválidos: piezas por impresión ≥ 1, minutos y gramos > 0, líneas de "
                                      "esa pieza."}, status=400)
        return Response(resultado)
