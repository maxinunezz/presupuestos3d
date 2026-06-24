from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.safestring import mark_safe

from .models import Maquina, ProductionJob, Tablero
from .scheduler import compute_schedule, material_forecast


def _fmt_dt(dt):
    if not dt:
        return "—"
    return timezone.localtime(dt).strftime("%d/%m %H:%M")


@admin.register(Maquina)
class MaquinaAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "supports_multicolor", "jobs_en_cola", "notes")
    list_editable = ("is_active", "supports_multicolor")
    search_fields = ("name",)

    @admin.display(description="Trabajos en cola")
    def jobs_en_cola(self, obj):
        return obj.jobs.filter(
            status__in=[ProductionJob.Status.PENDING, ProductionJob.Status.PRINTING]
        ).count()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Si la máquina queda inactiva, sus trabajos abiertos no se reprograman ni
        # se muestran en su cola: los liberamos (sin máquina) para que se vean en
        # "sin asignar" y se puedan reasignar a otra impresora.
        if not obj.is_active:
            open_jobs = obj.jobs.filter(
                status__in=[
                    ProductionJob.Status.PENDING,
                    ProductionJob.Status.PRINTING,
                ]
            )
            count = open_jobs.count()
            if count:
                open_jobs.update(machine=None)
                self.message_user(
                    request,
                    f"'{obj.name}' quedó inactiva: {count} trabajo(s) en cola se "
                    "liberaron (sin máquina). Reasignalos a otra impresora.",
                    level=messages.WARNING,
                )
        elif "supports_multicolor" in form.changed_data and not obj.supports_multicolor:
            # La máquina dejó de imprimir multicolor: los trabajos multicolor que
            # tenía en cola ya no los soporta. Los liberamos (sin máquina) para que
            # caigan en "sin asignar" y se reasignen a una máquina con AMS.
            multicolor_jobs = obj.jobs.filter(
                status__in=[
                    ProductionJob.Status.PENDING,
                    ProductionJob.Status.PRINTING,
                ],
                producto__is_multicolor=True,
            )
            count = multicolor_jobs.count()
            if count:
                multicolor_jobs.update(machine=None)
                self.message_user(
                    request,
                    f"'{obj.name}' dejó de imprimir multicolor: {count} trabajo(s) "
                    "multicolor en cola se liberaron (sin máquina). Reasignalos a "
                    "una impresora con AMS.",
                    level=messages.WARNING,
                )
        # Diferimos el recálculo: en una edición masiva desde el listado save_model
        # corre por cada fila, así que solo marcamos y persistimos una vez por request.
        request._needs_reschedule = True

    def _persist_if_needed(self, request):
        if getattr(request, "_needs_reschedule", False):
            from .scheduler import persist_schedule

            persist_schedule()
            request._needs_reschedule = False

    def changelist_view(self, request, extra_context=None):
        # Edición masiva (list_editable): super() guarda todas las filas (cada una
        # marca el flag en save_model); persistimos una sola vez al final.
        response = super().changelist_view(request, extra_context)
        self._persist_if_needed(request)
        return response

    def response_add(self, request, obj, post_url_continue=None):
        self._persist_if_needed(request)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        self._persist_if_needed(request)
        return super().response_change(request, obj)


@admin.register(ProductionJob)
class ProductionJobAdmin(admin.ModelAdmin):
    list_display = (
        "producto",
        "quantity",
        "presupuesto",
        "machine",
        "order",
        "status",
        "print_hours_display",
        "estimated_start_display",
        "estimated_print_end_display",
    )
    list_editable = ("machine", "order", "status")
    list_filter = ("machine", "status")
    search_fields = ("producto__name", "presupuesto__client_name")
    autocomplete_fields = ("presupuesto", "producto", "machine")
    readonly_fields = (
        "estimated_start",
        "estimated_print_end",
        "started_at",
        "finished_at",
        "created_at",
    )

    @admin.display(description="Horas impr.")
    def print_hours_display(self, obj):
        return f"{obj.print_hours} h"

    @admin.display(description="Inicio est.")
    def estimated_start_display(self, obj):
        return _fmt_dt(obj.estimated_start)

    @admin.display(description="Fin impr. est.")
    def estimated_print_end_display(self, obj):
        return _fmt_dt(obj.estimated_print_end)

    def save_model(self, request, obj, form, change):
        # Marca inicio real al empezar a imprimir.
        if obj.status == ProductionJob.Status.PRINTING and not obj.started_at:
            obj.started_at = timezone.now()
        super().save_model(request, obj, form, change)

        # Al marcar 'Impreso' se descuenta el material (una sola vez).
        if obj.status == ProductionJob.Status.DONE and not obj.stock_consumed:
            obj.consume_stock()
            self.message_user(
                request,
                f"Trabajo '{obj.producto}' impreso: material descontado del stock.",
            )

        # El estado del presupuesto sigue a la cola: si sus trabajos avanzaron,
        # el pedido avanza de etapa (En producción / Completado) automáticamente.
        if obj.presupuesto.sync_status_from_jobs():
            self.message_user(
                request,
                f"El presupuesto #{obj.presupuesto_id} pasó a "
                f"'{obj.presupuesto.get_status_display()}' según su cola de "
                "producción.",
            )

        # Cualquier cambio manual (máquina, orden, estado) recalcula la cola.
        from .scheduler import persist_schedule

        persist_schedule()


class ColaProduccion(ProductionJob):
    """Proxy para tener en el admin una página 'Cola de producción' (tablero)."""

    class Meta:
        proxy = True
        verbose_name = "Cola de producción"
        verbose_name_plural = "Cola de producción"


@admin.register(ColaProduccion)
class ColaProduccionAdmin(admin.ModelAdmin):
    """
    Tablero de solo lectura: muestra la cola de cada máquina con los tiempos
    estimados y, abajo, la cola total ordenada por inicio.
    """

    change_list_template = "admin/production/cola.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        now = timezone.now()
        schedule = compute_schedule(now)

        jobs = (
            ProductionJob.objects.filter(
                status__in=[
                    ProductionJob.Status.PENDING,
                    ProductionJob.Status.PRINTING,
                ]
            )
            .select_related("producto", "presupuesto", "machine")
            .order_by("machine", "order", "id")
        )

        def row(job):
            data = schedule.get(job.id, {})
            return {
                "producto": str(job.producto),
                "quantity": job.quantity,
                "cliente": job.presupuesto.client_name,
                "presupuesto_id": job.presupuesto_id,
                "status": job.get_status_display(),
                "print_hours": job.print_hours,
                "start": _fmt_dt(data.get("start")),
                "print_end": _fmt_dt(data.get("print_end")),
                "start_raw": data.get("start"),
            }

        # Colas por máquina (solo activas).
        active_ids = set(
            Maquina.objects.filter(is_active=True).values_list("id", flat=True)
        )
        machines = []
        for machine in Maquina.objects.filter(is_active=True):
            mjobs = [row(j) for j in jobs if j.machine_id == machine.id]
            free = None
            ends = [
                schedule[j.id]["print_end"]
                for j in jobs
                if j.machine_id == machine.id and j.id in schedule
            ]
            if ends:
                free = _fmt_dt(max(ends))
            machines.append(
                {
                    "name": machine.name,
                    "jobs": mjobs,
                    "free_at": free or "Libre ahora",
                    "count": len(mjobs),
                }
            )

        # Trabajos sin máquina (sin asignar o en máquina inactiva).
        unassigned = [row(j) for j in jobs if j.machine_id not in active_ids]

        # Cola total: todos los trabajos ordenados por inicio estimado.
        total = sorted(
            (row(j) for j in jobs),
            key=lambda r: (r["start_raw"] is None, r["start_raw"] or now),
        )

        context = {
            **self.admin_site.each_context(request),
            "title": "Cola de producción",
            "machines": machines,
            "unassigned": unassigned,
            "total": total,
            "window": "07:00 a 23:00",
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)


@admin.register(Tablero)
class TableroAdmin(admin.ModelAdmin):
    """
    Tablero general de producción (solo lectura): qué se está imprimiendo y
    cuándo termina, próximas entregas, y qué materia prima hay que comprar
    según la cola.
    """

    change_list_template = "admin/production/tablero.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from budgets.models import Presupuesto

        now = timezone.now()
        schedule = compute_schedule(now)

        open_jobs = (
            ProductionJob.objects.filter(
                status__in=[
                    ProductionJob.Status.PENDING,
                    ProductionJob.Status.PRINTING,
                ]
            )
            .select_related("producto", "presupuesto", "machine")
            .order_by("machine", "order", "id")
        )

        # --- Trabajos sin máquina asignada (incluye los de máquinas inactivas) ---
        active_ids = set(
            Maquina.objects.filter(is_active=True).values_list("id", flat=True)
        )
        unassigned_jobs = [j for j in open_jobs if j.machine_id not in active_ids]
        unassigned = [
            {
                "producto": str(j.producto),
                "quantity": j.quantity,
                "cliente": j.presupuesto.client_name,
                "presupuesto_id": j.presupuesto_id,
                "print_hours": j.print_hours,
            }
            for j in unassigned_jobs
        ]

        # --- Qué está/entra en cada máquina (el primero de cada cola) ---
        machines = []
        total_pending_hours = sum(float(j.print_hours) for j in unassigned_jobs)
        for machine in Maquina.objects.filter(is_active=True):
            mjobs = [j for j in open_jobs if j.machine_id == machine.id]
            total_pending_hours += sum(float(j.print_hours) for j in mjobs)
            current = mjobs[0] if mjobs else None
            cur = None
            if current:
                data = schedule.get(current.id, {})
                cur = {
                    "producto": str(current.producto),
                    "quantity": current.quantity,
                    "cliente": current.presupuesto.client_name,
                    "presupuesto_id": current.presupuesto_id,
                    "printing": current.status == ProductionJob.Status.PRINTING,
                    "print_end": _fmt_dt(data.get("print_end")),
                }
            machines.append(
                {
                    "name": machine.name,
                    "current": cur,
                    "queue_count": len(mjobs),
                }
            )

        # --- Próximas entregas ---
        proximas = []
        presupuestos = (
            Presupuesto.objects.filter(
                status__in=[
                    Presupuesto.Status.APPROVED,
                    Presupuesto.Status.IN_PRODUCTION,
                ],
                due_date__isnull=False,
            )
            .order_by("due_date")
        )
        for p in presupuestos:
            proximas.append(
                {
                    "id": p.pk,
                    "cliente": p.client_name,
                    "status": p.get_status_display(),
                    "due_date": _fmt_dt(p.due_date),
                    "manual": p.due_date_is_manual,
                }
            )

        # --- Compra de materia prima ---
        forecast = material_forecast(now)

        def fc_rows(rows, unidad=None):
            out = []
            for r in rows:
                out.append(
                    {
                        "item": r["item"],
                        "stock": f'{r["stock"]:.0f}',
                        "needed": f'{r["needed"]:.0f}',
                        "shortfall": f'{r["shortfall"]:.0f}',
                        "unit": r.get("unit", unidad or "g"),
                        "runs_out_at": _fmt_dt(r["runs_out_at"]),
                    }
                )
            return out

        context = {
            **self.admin_site.each_context(request),
            "title": "Tablero de producción",
            "machines": machines,
            "unassigned": unassigned,
            "proximas": proximas,
            "buy_filaments": fc_rows(forecast["filaments"], "g"),
            "buy_aggregates": fc_rows(forecast["aggregates"]),
            "total_pending_hours": round(total_pending_hours, 1),
            "open_jobs_count": open_jobs.count(),
            "window": "07:00 a 23:00",
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)
