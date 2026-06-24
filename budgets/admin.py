from django.contrib import admin, messages
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.safestring import mark_safe

from production.models import ProductionJob

from .metrics import PERIODS, build_metrics, export_xlsx, template_context
from .models import (
    Metricas,
    Presupuesto,
    PresupuestoItem,
    PresupuestoNotApprovableError,
    Producto,
    ProductoAggregateLine,
    ProductoFilamentLine,
)
from .pdf import (
    presupuesto_pdf_filename,
    render_presupuesto_pdf,
)


# ===========================================================================
#  COSTEO DE PRODUCTOS
# ===========================================================================


class ProductoFilamentLineInline(admin.TabularInline):
    model = ProductoFilamentLine
    extra = 1
    autocomplete_fields = ("filament",)
    readonly_fields = ("line_cost_display",)
    fields = ("filament", "grams_used", "unit_cost", "line_cost_display")

    @admin.display(description="Costo de línea")
    def line_cost_display(self, obj):
        return f"${obj.line_cost}" if obj.pk else "-"


class ProductoAggregateLineInline(admin.TabularInline):
    model = ProductoAggregateLine
    extra = 1
    autocomplete_fields = ("aggregate",)
    readonly_fields = ("line_cost_display",)
    fields = ("aggregate", "quantity", "unit_cost", "line_cost_display")

    @admin.display(description="Costo de línea")
    def line_cost_display(self, obj):
        return f"${obj.line_cost}" if obj.pk else "-"


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "unit_cost_display",
        "unit_price_display",
        "is_multicolor",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "is_multicolor")
    search_fields = ("name", "description")
    inlines = (ProductoFilamentLineInline, ProductoAggregateLineInline)
    readonly_fields = ("costs_summary",)

    fieldsets = (
        (None, {"fields": ("name", "description", "is_multicolor", "is_active")}),
        (
            "Impresión y máquina",
            {"fields": ("print_time_hours", "machine_cost_per_hour", "waste_percent")},
        ),
        (
            "Mano de obra / post-proceso",
            {"fields": ("post_processing_hours", "labor_cost_per_hour")},
        ),
        ("Precio", {"fields": ("margin_percent", "round_to", "costs_summary")}),
        (
            "Archivo del modelo",
            {
                "classes": ("collapse",),
                "fields": ("gcode", "model_file"),
            },
        ),
    )

    @admin.display(description="Costo/pieza")
    def unit_cost_display(self, obj):
        return f"${obj.unit_cost}"

    @admin.display(description="Precio/pieza")
    def unit_price_display(self, obj):
        return f"${obj.unit_price}"

    @admin.display(description="Resumen de costos")
    def costs_summary(self, obj):
        if not obj.pk:
            return "Guardá el producto para ver el resumen de costos."
        return mark_safe(
            "<b>Por pieza:</b><br>"
            f"&nbsp;&nbsp;Material: ${obj.material_cost} "
            f"(+ merma ${obj.material_waste_cost})<br>"
            f"&nbsp;&nbsp;Agregados: ${obj.aggregate_cost}<br>"
            f"&nbsp;&nbsp;Máquina: ${obj.machine_cost}<br>"
            f"&nbsp;&nbsp;Mano de obra: ${obj.labor_cost}<br>"
            f"&nbsp;&nbsp;<b>Costo por pieza: ${obj.unit_cost}</b><br>"
            f"&nbsp;&nbsp;Margen: {obj.margin_percent}%<br>"
            f"&nbsp;&nbsp;<b>PRECIO DE VENTA: ${obj.unit_price}</b>"
        )


# ===========================================================================
#  PRESUPUESTOS
# ===========================================================================


class PresupuestoItemInline(admin.TabularInline):
    model = PresupuestoItem
    extra = 1
    autocomplete_fields = ("producto",)
    readonly_fields = ("line_total_display",)
    fields = ("producto", "quantity", "unit_price", "line_total_display")

    @admin.display(description="Importe")
    def line_total_display(self, obj):
        return f"${obj.line_total}" if obj.pk else "-"


class ProductionJobInline(admin.TabularInline):
    model = ProductionJob
    extra = 0
    autocomplete_fields = ("producto", "machine")
    fields = (
        "producto",
        "quantity",
        "machine",
        "order",
        "status",
        "print_hours_display",
        "estimated_start",
        "estimated_print_end",
    )
    readonly_fields = (
        "print_hours_display",
        "estimated_start",
        "estimated_print_end",
    )

    @admin.display(description="Horas impr.")
    def print_hours_display(self, obj):
        return f"{obj.print_hours} h" if obj.pk else "-"


@admin.register(Presupuesto)
class PresupuestoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "client_name",
        "status",
        "total_pieces",
        "total_display",
        "due_date_display",
        "pdf_button",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("client_name", "description")
    inlines = (PresupuestoItemInline, ProductionJobInline)
    readonly_fields = (
        "approved_at",
        "sent_at",
        "production_started_at",
        "production_finished_at",
        "completed_at",
        "totals_summary",
        "production_summary",
        "pdf_link",
    )
    actions = ("approve_presupuestos",)

    fieldsets = (
        (None, {"fields": ("client_name", "description", "status")}),
        ("Precio del pedido", {"fields": ("fixed_cost", "round_to", "totals_summary")}),
        (
            "Producción y entrega",
            {
                "fields": (
                    "production_summary",
                    "due_date",
                    "due_date_is_manual",
                )
            },
        ),
        (
            "Fechas",
            {
                "classes": ("collapse",),
                "fields": (
                    "sent_at",
                    "approved_at",
                    "production_started_at",
                    "production_finished_at",
                    "completed_at",
                ),
            },
        ),
        ("Documento", {"fields": ("pdf_link",)}),
    )

    # --- PDF ---
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:pk>/pdf/",
                self.admin_site.admin_view(self.presupuesto_pdf_view),
                name="budgets_presupuesto_pdf",
            ),
        ]
        return custom + urls

    def presupuesto_pdf_view(self, request, pk):
        presupuesto = self.get_object(request, pk)
        if presupuesto is None:
            return HttpResponse("Presupuesto no encontrado.", status=404)
        pdf_bytes = render_presupuesto_pdf(presupuesto)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{presupuesto_pdf_filename(presupuesto)}"'
        )
        return response

    @admin.display(description="PDF")
    def pdf_button(self, obj):
        url = reverse("admin:budgets_presupuesto_pdf", args=[obj.pk])
        return mark_safe(
            f'<a class="button" href="{url}" target="_blank">PDF cliente</a>'
        )

    @admin.display(description="PDF para el cliente")
    def pdf_link(self, obj):
        if not obj.pk:
            return "Guardá el presupuesto para poder generar el PDF."
        url = reverse("admin:budgets_presupuesto_pdf", args=[obj.pk])
        return mark_safe(
            f'<a class="button" href="{url}" target="_blank">Descargar PDF para el cliente</a>'
        )

    @admin.display(description="Total pedido")
    def total_display(self, obj):
        return f"${obj.total}"

    @admin.display(description="Entrega")
    def due_date_display(self, obj):
        if not obj.due_date:
            return "—"
        fecha = timezone.localtime(obj.due_date).strftime("%d/%m %H:%M")
        return f"{fecha}{' ✋' if obj.due_date_is_manual else ''}"

    @admin.display(description="Producción y entrega")
    def production_summary(self, obj):
        if not obj.pk:
            return "Guardá el presupuesto y aprobalo para generar la cola."
        jobs = obj.jobs.select_related("producto", "machine").all()
        if not jobs:
            return mark_safe(
                "Todavía no hay trabajos de producción. Se generan al "
                "<b>aprobar</b> el presupuesto."
            )
        rows = ""
        for job in jobs:
            maquina = job.machine.name if job.machine else "(sin máquina)"
            fin = (
                timezone.localtime(job.estimated_print_end).strftime("%d/%m %H:%M")
                if job.estimated_print_end
                else "—"
            )
            rows += (
                f"&nbsp;&nbsp;{job.producto} ×{job.quantity} → <b>{maquina}</b> "
                f"({job.print_hours} h, fin impr. {fin})<br>"
            )
        entrega = (
            timezone.localtime(obj.estimated_delivery).strftime("%d/%m/%Y %H:%M")
            if obj.estimated_delivery
            else "—"
        )
        return mark_safe(
            f"{rows}"
            f"&nbsp;&nbsp;Impresión total: <b>{obj.total_print_hours} h</b><br>"
            f"&nbsp;&nbsp;Post-proceso total: <b>{obj.total_post_processing_hours} h</b><br>"
            f"&nbsp;&nbsp;<b>ENTREGA ESTIMADA: {entrega}</b>"
        )

    def save_model(self, request, obj, form, change):
        # Si el usuario tocó la fecha de entrega a mano, la marcamos como manual
        # para que el recalculo de la cola no la pise.
        if change and "due_date" in form.changed_data:
            obj.due_date_is_manual = bool(obj.due_date)
        # Guardamos el estado anterior para detectar el cambio de estado y
        # disparar sus efectos (fechas + cola) en save_related, cuando ya
        # están guardados los ítems. En un alta, el estado previo es Borrador.
        if change:
            request._old_presupuesto_status = (
                type(obj).objects.filter(pk=obj.pk)
                .values_list("status", flat=True)
                .first()
            )
        else:
            request._old_presupuesto_status = Presupuesto.Status.DRAFT
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance

        # Si el estado cambió desde el dropdown, seteamos las fechas y
        # disparamos los efectos (al aprobar: genera la cola de producción).
        old_status = getattr(request, "_old_presupuesto_status", None)
        if old_status is not None and old_status != obj.status:
            shortages = obj.apply_status_change(old_status)
            if obj.status == Presupuesto.Status.APPROVED:
                if shortages:
                    items = ", ".join(s["item"] for s in shortages)
                    self.message_user(
                        request,
                        f"Presupuesto #{obj.pk} aprobado y en cola. Ojo: el stock "
                        f"actual no alcanza para: {items} (se descuenta al imprimir "
                        f"cada trabajo).",
                        level=messages.WARNING,
                    )
                else:
                    self.message_user(
                        request,
                        f"Presupuesto #{obj.pk} aprobado: se generó la cola de "
                        "producción.",
                    )

        # Si algún trabajo quedó marcado como "Impreso" desde el inline, hay que
        # descontar su material (consume_stock no se dispara solo por el formset).
        for job in obj.jobs.filter(
            status=ProductionJob.Status.DONE, stock_consumed=False
        ):
            job.consume_stock()
            self.message_user(
                request,
                f"Trabajo '{job.producto}' impreso: material descontado del stock.",
            )

        # El estado del presupuesto sigue a la cola de producción: si los
        # trabajos avanzaron (imprimiendo / impresos), el pedido avanza de etapa.
        if obj.sync_status_from_jobs():
            self.message_user(
                request,
                f"El pedido pasó a '{obj.get_status_display()}' según el estado "
                "de sus trabajos de producción.",
            )

        # Tras guardar trabajos/ítems, recalcula cola y entrega estimada.
        if obj.jobs.exists():
            obj.refresh_delivery()

    @admin.display(description="Resumen del presupuesto")
    def totals_summary(self, obj):
        if not obj.pk:
            return "Guardá el presupuesto y agregá productos para ver el total."
        rows = "".join(
            f"&nbsp;&nbsp;{item.producto.name} × {item.quantity} "
            f"(${item.effective_unit_price} c/u): ${item.line_total}<br>"
            for item in obj.items.select_related("producto").all()
        )
        return mark_safe(
            f"{rows or '&nbsp;&nbsp;(sin productos todavía)<br>'}"
            f"&nbsp;&nbsp;Productos: ${obj.items_total}<br>"
            f"&nbsp;&nbsp;Costo fijo: ${obj.fixed_cost}<br>"
            f"&nbsp;&nbsp;Subtotal: ${obj.subtotal}<br>"
            f"&nbsp;&nbsp;<b>TOTAL: ${obj.total}</b> "
            f"({obj.total_pieces} pieza/s)"
        )

    @admin.action(description="Aprobar presupuesto(s) y generar cola de producción")
    def approve_presupuestos(self, request, queryset):
        for presupuesto in queryset:
            try:
                shortages = presupuesto.approve()
            except PresupuestoNotApprovableError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue

            if shortages:
                items = ", ".join(s["item"] for s in shortages)
                self.message_user(
                    request,
                    f"Presupuesto #{presupuesto.pk} aprobado y en cola. Ojo: el "
                    f"stock actual no alcanza para: {items} (se descuenta al "
                    f"imprimir cada trabajo).",
                    level=messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    f"Presupuesto #{presupuesto.pk} aprobado y en cola de producción.",
                )


# ===========================================================================
#  MÉTRICAS (panel de KPIs)
# ===========================================================================


@admin.register(Metricas)
class MetricasAdmin(admin.ModelAdmin):
    """
    Panel de solo lectura con KPIs de ventas, producción e inventario por
    período (semana / mes / año), gráficos (Chart.js) y export a Excel.
    """

    change_list_template = "admin/budgets/metricas.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        period = request.GET.get("period", "month")
        if period not in PERIODS:
            period = "month"

        metrics = build_metrics(period)

        # Descarga de la planilla de Excel.
        if request.GET.get("export") == "xlsx":
            fname, content = export_xlsx(metrics)
            response = HttpResponse(
                content,
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            )
            response["Content-Disposition"] = f'attachment; filename="{fname}"'
            return response

        context = {
            **self.admin_site.each_context(request),
            "title": "Métricas",
            "periods": [
                {"key": k, "label": v["label"], "active": k == period}
                for k, v in PERIODS.items()
            ],
            **template_context(metrics),
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)
