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
            .order_by("producto__priority", "order", "id")
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
    Qué materia prima hace falta comprar, calculado SOBRE EL STOCK ACTUAL.

    El material se descuenta al APROBAR el pedido (no al imprimir), y la
    producción puede dejar el stock en negativo. Por eso el pronóstico ya no
    proyecta consumo futuro: mira el stock real que quedó después de aprobar y
    marca todo lo que cayó por debajo de su mínimo (o quedó en negativo). Para
    cada insumo calcula:
      - stock actual (puede ser negativo si se sobre-comprometió)
      - nivel objetivo (su stock mínimo)
      - faltante = cuánto comprar para volver al mínimo
      - cuándo se necesita (inicio estimado del primer trabajo en cola que lo
        usa; ahí hay que tenerlo físicamente en la máquina)

    Devuelve {"filaments": [...], "aggregates": [...]} solo con lo que falta.
    """
    from inventory.models import Aggregate, Filament

    from .models import ProductionJob

    now = now or timezone.now()
    schedule = compute_schedule(now)

    # Primer trabajo en cola que usa cada insumo -> "comprá antes de esta fecha".
    open_jobs = list(
        ProductionJob.objects.filter(
            status__in=[ProductionJob.Status.PENDING, ProductionJob.Status.PRINTING],
        )
        .select_related("producto", "pieza")
        .prefetch_related(
            "producto__piezas__filament_lines__filament",
            "producto__aggregate_lines__aggregate",
            "pieza__filament_lines__filament",
        )
    )
    open_jobs.sort(key=lambda j: schedule.get(j.id, {}).get("start") or now)

    fil_when: dict[int, datetime] = {}
    agg_when: dict[int, datetime] = {}
    for job in open_jobs:
        start = schedule.get(job.id, {}).get("start")
        if start is None:
            continue
        if job.pieza:
            fids = {line.filament_id for line in job.pieza.filament_lines.all()}
        else:
            fids = {fil.id for fil, _ in job.producto.aggregated_filament()}
        for fid in fids:
            fil_when.setdefault(fid, start)
        for line in job.producto.aggregate_lines.all():
            agg_when.setdefault(line.aggregate_id, start)

    filaments = []
    for f in Filament.objects.filter(is_active=True):
        target = max(f.min_stock, Decimal("0"))
        shortfall = target - f.stock_grams
        if shortfall <= 0:
            continue
        filaments.append(
            {
                "item": str(f),
                "stock": f.stock_grams,
                "needed": target,
                "shortfall": shortfall,
                "runs_out_at": fil_when.get(f.id),
            }
        )

    aggregates = []
    for a in Aggregate.objects.filter(is_active=True):
        target = max(a.min_stock, Decimal("0"))
        shortfall = target - a.stock_quantity
        if shortfall <= 0:
            continue
        aggregates.append(
            {
                "item": str(a),
                "unit": a.get_unit_display(),
                "stock": a.stock_quantity,
                "needed": target,
                "shortfall": shortfall,
                "runs_out_at": agg_when.get(a.id),
            }
        )

    # Más urgente primero: lo que ya está en negativo, después lo que tiene un
    # trabajo en cola más próximo, y al final lo que no tiene cola asociada.
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
