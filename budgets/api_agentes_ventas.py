"""
Ventas para Roque (solo lectura, mismo token y mismas reglas que budgets/api_agentes.py: solo GET, falla cerrado).

- GET productos/?q=estrella          → buscar productos del costeo (con coincidencia exacta marcada)
- GET productos/<id>/                → costeo de un producto (precio general, costo, gramos, horas, post-proceso)
- GET simular-pedido/?items=12:50:1500,15:10:800
                                      → qué pasaría si se aprobara: diseño, precio, filamento e insumos que faltan,
                                        impresoras y fin de impresión. NO guarda nada: usa las mismas reglas que
                                        Presupuesto.approve() (stock de terminados, stock de piezas, corridas de
                                        gcode, merma, impresora recomendada) sobre una copia en memoria.
- GET precio-rapido/?material=PLA&gramos=120&horas=5&post_min=10
                                      → precio orientativo con la fórmula del costeo y el margen promedio actual.
"""

import math
from collections import defaultdict
from decimal import ROUND_CEILING, Decimal, InvalidOperation

from django.db.models import Avg, Q
from django.utils import timezone
from rest_framework.response import Response

from inventory.models import Filament
from production.scheduler import machine_free_times, recommend_machine

from .api_agentes import _VistaAgentes, _dinero
from .models import Producto, _dec

CENT = Decimal("0.01")


def _productos_qs():
    return Producto.objects.filter(is_active=True).prefetch_related(
        "producto_piezas__pieza__filament_lines__filament", "aggregate_lines__aggregate"
    )


def _completo(p):
    return len(p.producto_piezas.all()) > 0 and _dec(p.sale_price) > 0


def _resumen(p, q=""):
    return {
        "id": p.id, "nombre": p.name, "sku": p.sku, "diseno_listo": p.diseno_listo,
        "tiene_precio": _dec(p.sale_price) > 0, "costeo_completo": _completo(p),
        "exacto": bool(q) and (p.name.strip().lower() == q.strip().lower() or (p.sku or "").lower() == q.strip().lower()),
    }


class ProductosAgentes(_VistaAgentes):
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        qs = _productos_qs()
        if q:
            filtro = Q(sku__iexact=q) | Q(name__icontains=q)
            for palabra in [w for w in q.split() if len(w) > 2]:
                filtro |= Q(name__icontains=palabra)
            qs = qs.filter(filtro)
        datos = [_resumen(p, q) for p in qs[:200]]
        # Primero la coincidencia exacta, después las que contienen todas las palabras, después el resto.
        palabras = [w.lower() for w in q.split()]
        datos.sort(key=lambda d: (not d["exacto"], -sum(w in d["nombre"].lower() for w in palabras), d["nombre"]))
        return Response({"q": q, "productos": datos[:10]})


def _costeo(p):
    return {
        **_resumen(p), "precio_general": _dinero(p.sale_price), "costo_unitario": _dinero(p.unit_cost_avg),
        "margen_pct": str(p.margin_percent), "horas_maquina": str(p.total_machine_hours),
        "post_min": str(_dec(p.post_processing_minutes)), "stock_terminado": p.stock_quantity,
        "filamentos": [{"id": f.id, "nombre": str(f), "material": f.material_type, "color": f.color,
                        "gramos": str(p.filament_grams_needed(g, 1))} for f, g in p.aggregated_filament()],
        "insumos": [{"id": li.aggregate_id, "nombre": str(li.aggregate), "cantidad": str(li.quantity)}
                    for li in p.aggregate_lines.all()],
    }


class ProductoAgentes(_VistaAgentes):
    def get(self, request, pk):
        p = _productos_qs().filter(pk=pk).first()
        if not p:
            return Response({"error": "No existe ese producto."}, status=404)
        return Response(_costeo(p))


def _leer_items(texto):
    """'12:50:1500,15:10' → [(12, 50, Decimal('1500')), (15, 10, None)]"""
    items = []
    for parte in (texto or "").split(","):
        campos = parte.strip().split(":")
        if len(campos) < 2:
            continue
        try:
            precio = Decimal(campos[2]) if len(campos) > 2 and campos[2] else None
            items.append((int(campos[0]), int(campos[1]), precio))
        except (ValueError, InvalidOperation):
            raise ValueError(f"Ítem inválido: {parte!r}")
    return items


class SimularPedidoAgentes(_VistaAgentes):
    def get(self, request):
        try:
            pedidos = _leer_items(request.query_params.get("items"))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        if not pedidos:
            return Response({"error": "Faltan ítems: ?items=producto:cantidad:precio"}, status=400)
        productos = {p.id: p for p in _productos_qs().filter(pk__in=[i[0] for i in pedidos])}
        faltan = [i[0] for i in pedidos if i[0] not in productos]
        if faltan:
            return Response({"error": f"No existen los productos {faltan}"}, status=404)

        ahora = timezone.now()
        pieza_usada = defaultdict(int)       # stock de piezas compartido entre ítems
        gramos = defaultdict(Decimal)        # filamento id → gramos necesarios (con merma)
        insumos = defaultdict(Decimal)       # agregado id → cantidad
        objetos = {}
        trabajos = []                        # (prioridad, horas, ams, nombre)
        items_out, total, costo, horas_post = [], Decimal("0"), Decimal("0"), Decimal("0")

        for pid, cantidad, precio in pedidos:
            p = productos[pid]
            general = _dec(p.sale_price)
            precio = precio if precio is not None else general
            desde_terminados = min(p.stock_quantity, cantidad)
            producir = cantidad - desde_terminados
            for pp in p.producto_piezas.all():
                pieza = pp.pieza
                requeridas = pp.units_needed * producir
                disponibles = max(pieza.stock_quantity - pieza_usada[pieza.id], 0)
                desde_stock = min(disponibles, requeridas)
                pieza_usada[pieza.id] += desde_stock
                imprimir = requeridas - desde_stock
                if imprimir <= 0:
                    continue
                corridas = math.ceil(imprimir / (pieza.pieces_per_gcode or 1))
                trabajos.append((p.priority, _dec(pieza.print_time_minutes) / Decimal("60") * corridas,
                                 pieza.requires_ams, pieza.name))
                for linea in pieza.filament_lines.all():
                    gramos[linea.filament_id] += _dec(linea.grams_used) * corridas * p.waste_multiplier
                    objetos[("f", linea.filament_id)] = linea.filament
            for linea in p.aggregate_lines.all():
                insumos[linea.aggregate_id] += p.aggregate_qty_needed(linea, producir)
                objetos[("a", linea.aggregate_id)] = linea.aggregate
            horas_post += _dec(p.post_processing_minutes) * producir / Decimal("60")
            costo_item = p.unit_cost_avg * cantidad
            total += precio * cantidad
            costo += costo_item
            items_out.append({
                **_resumen(p), "cantidad": cantidad, "desde_terminados": desde_terminados, "a_producir": producir,
                "precio_vendido": _dinero(precio), "precio_general": _dinero(general),
                "comparacion": ("sin_precio_general" if general <= 0 else
                                "igual" if precio == general else "mayor" if precio > general else "menor"),
                "total": _dinero(precio * cantidad), "costo": _dinero(costo_item),
                "ganancia": _dinero(precio * cantidad - costo_item),
            })

        # Cola: misma recomendación que al aprobar, sobre una copia de los horarios libres (no se guarda nada).
        libres = dict(machine_free_times(ahora))
        trabajos.sort(key=lambda t: (t[0], -t[1]))
        fin, maquinas, sin_maquina, horas_impresion = None, set(), [], Decimal("0")
        for _, horas, ams, nombre in trabajos:
            horas_impresion += horas
            maquina, libres = recommend_machine(horas, now=ahora, _free_cache=libres, requires_multicolor=ams)
            if maquina is None:
                sin_maquina.append(nombre)
                continue
            maquinas.add(maquina.name)
            fin = max(fin, libres[maquina.id]) if fin else libres[maquina.id]

        filamentos = []
        for fid, necesita in gramos.items():
            f = objetos[("f", fid)]
            necesita = necesita.quantize(CENT)
            falta = max(necesita - max(f.stock_grams, Decimal("0")), Decimal("0"))
            filamentos.append({"id": fid, "nombre": str(f), "material": f.material_type, "color": f.color,
                               "necesita_g": str(necesita), "stock_g": str(f.stock_grams), "falta_g": str(falta),
                               "costo_kg": _dinero(f.cost_per_kg)})
        insumos_out = []
        for aid, necesita in insumos.items():
            a = objetos[("a", aid)]
            falta = max(necesita - max(a.stock_quantity, Decimal("0")), Decimal("0"))
            insumos_out.append({"id": aid, "nombre": str(a), "unidad": a.unit, "necesita": str(necesita),
                                "stock": str(a.stock_quantity), "falta": str(falta), "costo_unidad": _dinero(a.cost_per_unit)})
        return Response({
            "items": items_out, "filamentos": filamentos, "insumos": insumos_out,
            "impresion": {"horas": str(horas_impresion.quantize(CENT)), "trabajos": len(trabajos),
                          "fin": (fin or ahora).isoformat(), "maquinas": sorted(maquinas), "sin_maquina": sin_maquina},
            "horas_post": str(horas_post.quantize(CENT)),
            "totales": {"total": _dinero(total), "costo": _dinero(costo), "ganancia": _dinero(total - costo),
                        "margen_pct": str(((total - costo) / costo * 100).quantize(CENT)) if costo > 0 else "0"},
        })


class PrecioRapidoAgentes(_VistaAgentes):
    def get(self, request):
        qp = request.query_params
        try:
            gramos = Decimal(qp.get("gramos") or "0")
            horas = Decimal(qp.get("horas") or "0")
            post_min = Decimal(qp.get("post_min") or "0")
        except InvalidOperation:
            return Response({"error": "gramos, horas y post_min tienen que ser números"}, status=400)
        if gramos <= 0 and horas <= 0:
            return Response({"error": "Faltan los gramos o las horas de impresión"}, status=400)
        material = (qp.get("material") or "PLA").strip()
        fil = Filament.objects.filter(is_active=True, material_type__iexact=material)
        if not fil.exists():
            fil = Filament.objects.filter(is_active=True)
        costo_kg = _dec(fil.aggregate(x=Avg("cost_per_kg"))["x"])
        productos = list(_productos_qs())
        prom = Producto.objects.filter(is_active=True).aggregate(
            maq=Avg("machine_cost_per_hour"), mo=Avg("labor_cost_per_hour"), merma=Avg("waste_percent"))
        maq, mo = _dec(prom["maq"] or 300), _dec(prom["mo"] or 5000)
        merma = _dec(prom["merma"] if prom["merma"] is not None else 5)
        # Margen promedio de los productos que hoy dejan ganancia (los que pierden plata no son referencia).
        margenes = [m for m in (p.margin_percent for p in productos if _dec(p.sale_price) > 0 and p.unit_cost_avg > 0)
                    if m > 0]
        margen = (sum(margenes) / len(margenes)) if margenes else Decimal("100")
        costo = (gramos / 1000 * costo_kg * (1 + merma / 100) + horas * maq + post_min / 60 * mo).quantize(CENT)
        precio = (costo * (1 + margen / 100) / 100).to_integral_value(rounding=ROUND_CEILING) * 100
        return Response({
            "precio": _dinero(precio), "costo": _dinero(costo), "margen_pct": str(margen.quantize(CENT)),
            "base": {"material": material, "costo_kg": _dinero(costo_kg), "merma_pct": str(merma.quantize(CENT)),
                     "maquina_hora": _dinero(maq), "mano_obra_hora": _dinero(mo), "productos_para_margen": len(margenes)},
        })
