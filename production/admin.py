from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from .models import HistorialImpresion, Maquina, ProductionJob, Tablero
from .scheduler import compute_schedule, material_forecast


def _fmt_dt(dt):
    if not dt:
        return "—"
    return timezone.localtime(dt).strftime("%d/%m %H:%M")


class HistorialImpresionInline(admin.TabularInline):
    """Historial de impresiones de la máquina (solo lectura)."""

    model = HistorialImpresion
    verbose_name = _("Impresión del historial")
    verbose_name_plural = _("Historial de impresiones")
    extra = 0
    can_delete = False
    fields = ("finalizado_el", "titulo", "cantidad", "horas_impresion", "estado")
    readonly_fields = ("finalizado_el", "titulo", "cantidad", "horas_impresion", "estado")
    ordering = ("-finalizado_el",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Maquina)
class MaquinaAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "supports_multicolor",
        "jobs_en_cola",
        "cost_per_hour",
        "total_hours_printed",
        "depreciacion_display",
        "notes",
    )
    list_editable = ("is_active", "supports_multicolor", "cost_per_hour")
    search_fields = ("name",)
    inlines = [HistorialImpresionInline]
    readonly_fields = ("total_hours_printed", "depreciacion_display", "created_at")
    fields = (
        "name",
        "is_active",
        "supports_multicolor",
        "cost_per_hour",
        "total_hours_printed",
        "depreciacion_display",
        "notes",
        "created_at",
    )

    @admin.display(description=_("Trabajos en cola"))
    def jobs_en_cola(self, obj):
        return obj.jobs.filter(
            status__in=[ProductionJob.Status.PENDING, ProductionJob.Status.PRINTING]
        ).count()

    @admin.display(description=_("Depreciación acumulada"))
    def depreciacion_display(self, obj):
        return f"$ {obj.accumulated_depreciation:,.2f}"

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
                    gettext(
                        "'%(name)s' quedó inactiva: %(count)s trabajo(s) en cola se "
                        "liberaron (sin máquina). Reasignalos a otra impresora."
                    )
                    % {"name": obj.name, "count": count},
                    level=messages.WARNING,
                )
        elif "supports_multicolor" in form.changed_data and not obj.supports_multicolor:
            # La máquina dejó de imprimir multicolor: los trabajos multicolor que
            # tenía en cola ya no los soporta. Los liberamos (sin máquina) para que
            # caigan en "sin asignar" y se reasignen a una máquina con AMS.
            from django.db.models import Q

            multicolor_jobs = obj.jobs.filter(
                Q(pieza__requires_ams=True) | Q(producto__is_multicolor=True),
                status__in=[
                    ProductionJob.Status.PENDING,
                    ProductionJob.Status.PRINTING,
                ],
            )
            count = multicolor_jobs.count()
            if count:
                multicolor_jobs.update(machine=None)
                self.message_user(
                    request,
                    gettext(
                        "'%(name)s' dejó de imprimir multicolor: %(count)s trabajo(s) "
                        "multicolor en cola se liberaron (sin máquina). Reasignalos a "
                        "una impresora con AMS."
                    )
                    % {"name": obj.name, "count": count},
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
        "pieza",
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
    search_fields = ("producto__name", "pieza__name", "presupuesto__client_name")
    autocomplete_fields = ("presupuesto", "producto", "pieza", "machine")
    readonly_fields = (
        "estimated_start",
        "estimated_print_end",
        "started_at",
        "finished_at",
        "created_at",
    )
    actions = ("marcar_obsoleta",)

    @admin.action(description=_("Marcar impresión obsoleta (reimprimir)"))
    def marcar_obsoleta(self, request, queryset):
        """
        Marca las impresiones seleccionadas como obsoletas (salieron mal) y las
        manda a reimprimir. Muestra un paso intermedio para cargar a mano los
        gramos de filamento que se perdieron en cada impresión fallida.
        """
        from decimal import Decimal, InvalidOperation

        elegibles = [
            job
            for job in queryset.select_related("pieza", "producto", "presupuesto")
            if job.pieza_id
            and job.status in (ProductionJob.Status.PENDING, ProductionJob.Status.PRINTING)
            and job.stock_consumed
        ]
        descartados = [j for j in queryset if j not in elegibles]
        if descartados:
            self.message_user(
                request,
                gettext(
                    "%(count)s trabajo(s) no se pueden marcar obsoletos "
                    "(deben ser por pieza, En cola/Imprimiendo y con material "
                    "descontado). Se ignoraron."
                )
                % {"count": len(descartados)},
                level=messages.WARNING,
            )
        if not elegibles:
            return None

        # Paso 2: el usuario confirmó y cargó los gramos perdidos.
        if request.POST.get("apply_obsoleta"):
            for job in elegibles:
                raw = request.POST.get(f"scrap_{job.pk}", "0") or "0"
                try:
                    scrap = Decimal(raw.replace(",", "."))
                except (InvalidOperation, AttributeError):
                    scrap = Decimal("0")
                summary = job.mark_obsolete(scrap)
                devuelto = ", ".join(
                    gettext("%(grams)s g de %(item)s")
                    % {"grams": r["grams"], "item": r["item"]}
                    for r in summary["returned"]
                )
                self.message_user(
                    request,
                    gettext(
                        "Impresión '%(job)s' marcada obsoleta y devuelta a la cola: "
                        "se perdieron %(scrap)s g; volvieron al stock "
                        "%(devuelto)s. Se reimprime y vuelve a descontar el "
                        "material al terminar."
                    )
                    % {
                        "job": job,
                        "scrap": summary["scrap"],
                        "devuelto": devuelto or gettext("0 g"),
                    },
                )
            # Recalcula la cola (los trabajos volvieron a PENDING).
            from .scheduler import persist_schedule

            persist_schedule()
            return None

        # Paso 1: mostramos el formulario con el total de filamento por trabajo.
        filas = []
        for job in elegibles:
            total_g = sum((g for _, _, g in job.job_filament_grams()), Decimal("0"))
            filas.append({"job": job, "total_g": total_g})
        context = {
            **self.admin_site.each_context(request),
            "title": _("Marcar impresiones obsoletas"),
            "filas": filas,
            "action_name": "marcar_obsoleta",
            "selected": [j.pk for j in elegibles],
        }
        return TemplateResponse(
            request, "admin/production/marcar_obsoleta.html", context
        )

    @admin.display(description=_("Horas impr."))
    def print_hours_display(self, obj):
        return f"{obj.print_hours} h"

    @admin.display(description=_("Inicio est."))
    def estimated_start_display(self, obj):
        return _fmt_dt(obj.estimated_start)

    @admin.display(description=_("Fin impr. est."))
    def estimated_print_end_display(self, obj):
        return _fmt_dt(obj.estimated_print_end)

    def save_model(self, request, obj, form, change):
        # Marca inicio real al empezar a imprimir.
        if obj.status == ProductionJob.Status.PRINTING and not obj.started_at:
            obj.started_at = timezone.now()
        # Marca fin real al imprimirse (lo usa el panel de métricas de producción).
        if obj.status == ProductionJob.Status.DONE and not obj.finished_at:
            obj.finished_at = timezone.now()
        super().save_model(request, obj, form, change)

        # Al marcar 'Impreso': el material ya se descontó al aprobar; acá la
        # sobrante del último gcode se suma al stock de la pieza. (Para trabajos
        # del modo anterior sin pieza, recién acá se descuenta el material.)
        if obj.status == ProductionJob.Status.DONE:
            obj.consume_stock()  # no-op si ya se descontó al aprobar
            surplus = obj.register_surplus()
            if surplus:
                self.message_user(
                    request,
                    gettext(
                        "Trabajo '%(obj)s' impreso: %(surplus)s unidad(es) sobrante(s) "
                        "se sumaron al stock de la pieza."
                    )
                    % {"obj": obj, "surplus": surplus},
                )
            # Recalcula las horas impresas (y con ellas la depreciación) de la
            # máquina que ejecutó el trabajo.
            if obj.machine_id:
                obj.machine.recalc_printed_hours()
            # Guarda el trabajo impreso en el historial de su máquina.
            obj.register_history()

        # Al cancelar un trabajo también queda registrado en el historial de su
        # máquina (con estado Cancelado).
        if obj.status == ProductionJob.Status.CANCELLED:
            obj.register_history(estado=HistorialImpresion.Estado.CANCELADO)

        # El estado del presupuesto sigue a la cola: si sus trabajos avanzaron,
        # el pedido avanza de etapa (En producción / Completado) automáticamente.
        if obj.presupuesto.sync_status_from_jobs():
            self.message_user(
                request,
                gettext(
                    "El presupuesto #%(id)s pasó a "
                    "'%(status)s' según su cola de "
                    "producción."
                )
                % {
                    "id": obj.presupuesto_id,
                    "status": obj.presupuesto.get_status_display(),
                },
            )

        # Cualquier cambio manual (máquina, orden, estado) recalcula la cola.
        from .scheduler import persist_schedule

        persist_schedule()


class ColaProduccion(ProductionJob):
    """Proxy para tener en el admin una página 'Cola de producción' (tablero)."""

    class Meta:
        proxy = True
        verbose_name = _("Cola de producción")
        verbose_name_plural = _("Cola de producción")


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
            .order_by("machine", "producto__priority", "order", "id")
        )

        def row(job):
            data = schedule.get(job.id, {})
            return {
                "producto": str(job.producto),
                "priority": job.producto.get_priority_display(),
                "priority_high": job.producto.priority <= 2,
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
                    "free_at": free or gettext("Libre ahora"),
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
            "title": _("Cola de producción"),
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

        open_jobs = list(
            ProductionJob.objects.filter(
                status__in=[
                    ProductionJob.Status.PENDING,
                    ProductionJob.Status.PRINTING,
                ]
            )
            .select_related("producto", "presupuesto", "machine")
            .order_by("machine", "producto__priority", "order", "id")
        )

        # Separamos los trabajos por estado para que el tablero no llame "en cola"
        # a algo que ya se está imprimiendo (eso confundía: la máquina mostraba
        # "imprimiendo" pero el KPI lo contaba como en cola).
        printing_count = sum(
            1 for j in open_jobs if j.status == ProductionJob.Status.PRINTING
        )
        pending_count = sum(
            1 for j in open_jobs if j.status == ProductionJob.Status.PENDING
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

        # --- Máquinas inactivas (ej: rotas / en mantenimiento) ---
        # Las mostramos aparte con su nota, que es donde se anota qué está roto.
        inactive_machines = [
            {"name": m.name, "notes": m.notes}
            for m in Maquina.objects.filter(is_active=False)
        ]

        # Pedidos efectivamente en producción (al menos un trabajo imprimiéndose).
        # Es el estado real del presupuesto, no depende de que tenga fecha de entrega.
        in_production_count = Presupuesto.objects.filter(
            status=Presupuesto.Status.IN_PRODUCTION
        ).count()

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
            "title": _("Tablero de producción"),
            "machines": machines,
            "inactive_machines": inactive_machines,
            "unassigned": unassigned,
            "proximas": proximas,
            "buy_filaments": fc_rows(forecast["filaments"], "g"),
            "buy_aggregates": fc_rows(forecast["aggregates"]),
            "total_pending_hours": round(total_pending_hours, 1),
            "printing_count": printing_count,
            "pending_count": pending_count,
            "in_production_count": in_production_count,
            "window": "07:00 a 23:00",
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)
