"""
Motor de cola de producción.

Reglas:
- Cada máquina activa procesa su cola de trabajos en orden.
- Una máquina imprime de corrido (un trabajo puede cruzar la noche), PERO un
  trabajo nuevo solo se puede *arrancar* dentro de la ventana de carga
  (por defecto 07:00 a 23:00), porque alguien tiene que cargar la pieza.
  Si una máquina queda libre de madrugada, el siguiente arranca a las 07:00.
- El post-proceso no ocupa la máquina: se suma después, sobre la entrega.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

# Ventana de carga: desde LOAD_START se pueden arrancar trabajos; a partir de
# LOAD_END ya no se cargan nuevos (las máquinas siguen lo que tengan en curso).
LOAD_START = time(7, 0)
LOAD_END = time(23, 0)


def next_loadable(dt: datetime) -> datetime:
    """
    Devuelve el primer instante >= dt en el que se puede ARRANCAR un trabajo,
    respetando la ventana de carga. Trabaja en hora local.
    """
    local = timezone.localtime(dt)
    t = local.time()
    if LOAD_START <= t < LOAD_END:
        return dt
    # Antes de la ventana (madrugada): hoy a LOAD_START. Después (>= LOAD_END):
    # mañana a LOAD_START. Re-localizamos con make_aware para respetar la zona
    # horaria correctamente (incluso si alguna vez hubiera horario de verano).
    target_date = local.date()
    if t >= LOAD_END:
        target_date = target_date + timedelta(days=1)
    naive = datetime.combine(target_date, LOAD_START)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _add_hours(dt: datetime, hours) -> datetime:
    return dt + timedelta(hours=float(Decimal(str(hours or 0))))


def compute_schedule(now: datetime = None) -> dict:
    """
    Recalcula inicio y fin de impresión de cada trabajo abierto.

    Devuelve un dict job_id -> {"start": dt, "print_end": dt, "machine_id": id}.
    No guarda nada en la base: solo calcula. La impresión es continua; solo el
    arranque de cada trabajo respeta la ventana de carga.
    """
    from .models import Maquina, ProductionJob

    now = now or timezone.now()
    result = {}

    for machine in Maquina.objects.filter(is_active=True):
        cursor = now
        jobs = (
            ProductionJob.objects.filter(
                machine=machine,
                status__in=[
                    ProductionJob.Status.PENDING,
                    ProductionJob.Status.PRINTING,
                ],
            )
            .select_related("producto")
            .order_by("order", "id")
        )
        for job in jobs:
            if job.status == ProductionJob.Status.PRINTING and job.started_at:
                # Ya está imprimiendo: arranca en su inicio real.
                start = job.started_at
            else:
                start = next_loadable(max(cursor, now))
            print_end = _add_hours(start, job.print_hours)
            result[job.id] = {
                "start": start,
                "print_end": print_end,
                "machine_id": machine.id,
            }
            cursor = print_end

    return result


def machine_free_times(now: datetime = None, schedule: dict = None) -> dict:
    """
    Devuelve, por máquina activa, el instante en que queda libre según su cola
    actual: máximo print_end de sus trabajos, o `now` si no tiene nada.
    """
    from .models import Maquina

    now = now or timezone.now()
    schedule = schedule if schedule is not None else compute_schedule(now)

    free = {m.id: now for m in Maquina.objects.filter(is_active=True)}
    for data in schedule.values():
        mid = data["machine_id"]
        if mid in free and data["print_end"] > free[mid]:
            free[mid] = data["print_end"]
    return free


def recommend_machine(
    print_hours,
    now: datetime = None,
    _free_cache: dict = None,
    requires_multicolor: bool = False,
):
    """
    Recomienda la máquina que se libera antes. Si se pasa `_free_cache`
    (mutable), lo actualiza reservando el tiempo del trabajo, para poder
    recomendar varios trabajos seguidos de forma balanceada.

    Si `requires_multicolor` es True, solo considera máquinas que imprimen
    multicolor (AMS); si ninguna sirve, devuelve (None, free_cache) para que
    el trabajo quede sin asignar y se vea en el tablero.

    Devuelve (maquina, free_cache) o (None, free_cache) si no hay máquinas.
    """
    from .models import Maquina

    now = now or timezone.now()
    free = _free_cache if _free_cache is not None else machine_free_times(now)
    if not free:
        return None, free

    candidates = dict(free)
    if requires_multicolor:
        capable_ids = set(
            Maquina.objects.filter(
                is_active=True, supports_multicolor=True
            ).values_list("id", flat=True)
        )
        candidates = {mid: t for mid, t in free.items() if mid in capable_ids}
        if not candidates:
            return None, free

    best_id = min(candidates, key=lambda mid: candidates[mid])
    start = next_loadable(max(free[best_id], now))
    free[best_id] = _add_hours(start, print_hours)
    return Maquina.objects.get(pk=best_id), free


def material_forecast(now: datetime = None) -> dict:
    """
    Cruza la cola de producción con el stock actual para anticipar faltantes.

    Recorre los trabajos abiertos en orden de inicio estimado y va descontando
    el consumo de cada filamento/agregado. Para cada insumo calcula:
      - stock actual
      - consumo total que pide la cola
      - faltante (cuánto hay que comprar)
      - cuándo se agota (inicio estimado del trabajo que lo deja en negativo)

    Devuelve {"filaments": [...], "aggregates": [...]} solo con los insumos
    que se quedan cortos (faltante > 0).
    """
    from collections import defaultdict

    from inventory.models import Aggregate, Filament

    from .models import ProductionJob

    now = now or timezone.now()
    schedule = compute_schedule(now)

    jobs = list(
        ProductionJob.objects.filter(
            status__in=[ProductionJob.Status.PENDING, ProductionJob.Status.PRINTING]
        )
        .select_related("producto")
        .prefetch_related(
            "producto__filament_lines__filament",
            "producto__aggregate_lines__aggregate",
        )
    )
    jobs.sort(key=lambda j: schedule.get(j.id, {}).get("start") or now)

    fil_obj = {f.id: f for f in Filament.objects.all()}
    agg_obj = {a.id: a for a in Aggregate.objects.all()}
    fil_remaining = {fid: f.stock_grams for fid, f in fil_obj.items()}
    agg_remaining = {aid: a.stock_quantity for aid, a in agg_obj.items()}

    fil_needed = defaultdict(lambda: Decimal("0"))
    agg_needed = defaultdict(lambda: Decimal("0"))
    fil_runout = {}
    agg_runout = {}

    for job in jobs:
        start = schedule.get(job.id, {}).get("start")
        producto = job.producto
        for line in producto.filament_lines.all():
            need = producto.filament_grams_needed(line, job.quantity)
            fid = line.filament_id
            fil_needed[fid] += need
            if fid in fil_remaining:
                fil_remaining[fid] -= need
                if fil_remaining[fid] < 0 and fid not in fil_runout:
                    fil_runout[fid] = start
        for line in producto.aggregate_lines.all():
            need = producto.aggregate_qty_needed(line, job.quantity)
            aid = line.aggregate_id
            agg_needed[aid] += need
            if aid in agg_remaining:
                agg_remaining[aid] -= need
                if agg_remaining[aid] < 0 and aid not in agg_runout:
                    agg_runout[aid] = start

    filaments = []
    for fid, needed in fil_needed.items():
        f = fil_obj.get(fid)
        if f is None:
            continue
        shortfall = max(Decimal("0"), needed - f.stock_grams)
        if shortfall > 0:
            filaments.append(
                {
                    "item": str(f),
                    "stock": f.stock_grams,
                    "needed": needed,
                    "shortfall": shortfall,
                    "runs_out_at": fil_runout.get(fid),
                }
            )

    aggregates = []
    for aid, needed in agg_needed.items():
        a = agg_obj.get(aid)
        if a is None:
            continue
        shortfall = max(Decimal("0"), needed - a.stock_quantity)
        if shortfall > 0:
            aggregates.append(
                {
                    "item": str(a),
                    "unit": a.get_unit_display(),
                    "stock": a.stock_quantity,
                    "needed": needed,
                    "shortfall": shortfall,
                    "runs_out_at": agg_runout.get(aid),
                }
            )

    filaments.sort(key=lambda r: (r["runs_out_at"] is None, r["runs_out_at"] or now))
    aggregates.sort(key=lambda r: (r["runs_out_at"] is None, r["runs_out_at"] or now))
    return {"filaments": filaments, "aggregates": aggregates}


def persist_schedule(now: datetime = None):
    """
    Recalcula la cola y guarda el snapshot (estimated_start / estimated_print_end)
    en cada trabajo abierto. Devuelve el schedule calculado.
    """
    from .models import ProductionJob

    now = now or timezone.now()
    schedule = compute_schedule(now)

    for job_id, data in schedule.items():
        ProductionJob.objects.filter(pk=job_id).update(
            estimated_start=data["start"],
            estimated_print_end=data["print_end"],
        )
    return schedule
