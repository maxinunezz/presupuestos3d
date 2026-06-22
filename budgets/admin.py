from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils.safestring import mark_safe

from .models import (
    Budget,
    BudgetAggregateLine,
    BudgetFilamentLine,
    BudgetNotApprovableError,
)
from .pdf import budget_pdf_filename, render_budget_pdf


class BudgetFilamentLineInline(admin.TabularInline):
    model = BudgetFilamentLine
    extra = 1
    autocomplete_fields = ("filament",)
    readonly_fields = ("line_cost_display",)
    fields = ("filament", "grams_used", "unit_cost", "line_cost_display")

    @admin.display(description="Costo de línea")
    def line_cost_display(self, obj):
        if obj.pk:
            return f"${obj.line_cost}"
        return "-"


class BudgetAggregateLineInline(admin.TabularInline):
    model = BudgetAggregateLine
    extra = 1
    autocomplete_fields = ("aggregate",)
    readonly_fields = ("line_cost_display",)
    fields = ("aggregate", "quantity", "unit_cost", "line_cost_display")

    @admin.display(description="Costo de línea")
    def line_cost_display(self, obj):
        if obj.pk:
            return f"${obj.line_cost}"
        return "-"


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "client_name",
        "status",
        "quantity",
        "unit_cost_display",
        "unit_price_display",
        "total_display",
        "pdf_button",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("name", "client_name")
    inlines = (BudgetFilamentLineInline, BudgetAggregateLineInline)
    readonly_fields = ("approved_at", "costs_summary", "pdf_link")
    actions = ("approve_budgets", "duplicate_budgets")

    # --- URL propia para descargar el PDF ---
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:pk>/pdf/",
                self.admin_site.admin_view(self.budget_pdf_view),
                name="budgets_budget_pdf",
            ),
        ]
        return custom + urls

    def budget_pdf_view(self, request, pk):
        budget = self.get_object(request, pk)
        if budget is None:
            return HttpResponse("Presupuesto no encontrado.", status=404)
        pdf_bytes = render_budget_pdf(budget)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{budget_pdf_filename(budget)}"'
        )
        return response

    @admin.display(description="PDF")
    def pdf_button(self, obj):
        url = reverse("admin:budgets_budget_pdf", args=[obj.pk])
        return mark_safe(
            f'<a class="button" href="{url}" target="_blank">PDF cliente</a>'
        )

    @admin.action(description="Duplicar presupuesto(s) (re-cotiza a precios de hoy)")
    def duplicate_budgets(self, request, queryset):
        for budget in queryset:
            copy = budget.duplicate()
            self.message_user(
                request,
                f"Presupuesto #{budget.pk} duplicado como #{copy.pk} (Borrador).",
            )

    fieldsets = (
        (None, {"fields": ("client_name", "name", "description", "quantity", "status")}),
        (
            "Impresión y máquina",
            {"fields": ("print_time_hours", "machine_cost_per_hour", "waste_percent")},
        ),
        (
            "Mano de obra / post-proceso",
            {"fields": ("post_processing_hours", "labor_cost_per_hour")},
        ),
        (
            "Pedido y precio",
            {
                "fields": (
                    "fixed_cost",
                    "margin_percent",
                    "round_to",
                    "costs_summary",
                )
            },
        ),
        ("Aprobación", {"fields": ("approved_at",)}),
        ("Documento", {"fields": ("pdf_link",)}),
    )

    @admin.display(description="PDF para el cliente")
    def pdf_link(self, obj):
        if not obj.pk:
            return "Guardá el presupuesto para poder generar el PDF."
        url = reverse("admin:budgets_budget_pdf", args=[obj.pk])
        return mark_safe(
            f'<a class="button" href="{url}" target="_blank">Descargar PDF para el cliente</a>'
        )

    @admin.display(description="Costo/pieza")
    def unit_cost_display(self, obj):
        return f"${obj.unit_cost}"

    @admin.display(description="Precio/pieza")
    def unit_price_display(self, obj):
        return f"${obj.unit_price}"

    @admin.display(description="Total pedido")
    def total_display(self, obj):
        return f"${obj.total}"

    @admin.display(description="Resumen de costos")
    def costs_summary(self, obj):
        if not obj.pk:
            return "Guardá el presupuesto para ver el resumen de costos."
        return mark_safe(
            "<b>Por pieza:</b><br>"
            f"&nbsp;&nbsp;Material: ${obj.material_cost} "
            f"(+ merma ${obj.material_waste_cost})<br>"
            f"&nbsp;&nbsp;Agregados: ${obj.aggregate_cost}<br>"
            f"&nbsp;&nbsp;Máquina: ${obj.machine_cost}<br>"
            f"&nbsp;&nbsp;Mano de obra: ${obj.labor_cost}<br>"
            f"&nbsp;&nbsp;<b>Costo por pieza: ${obj.unit_cost}</b><br><br>"
            f"<b>Pedido ({obj.quantity} pieza/s):</b><br>"
            f"&nbsp;&nbsp;Producción ({obj.quantity}×${obj.unit_cost}): ${obj.production_cost}<br>"
            f"&nbsp;&nbsp;Costo fijo: ${obj.fixed_cost}<br>"
            f"&nbsp;&nbsp;Subtotal: ${obj.subtotal}<br>"
            f"&nbsp;&nbsp;Margen: {obj.margin_percent}%<br>"
            f"&nbsp;&nbsp;<b>TOTAL: ${obj.total}</b> "
            f"(${obj.unit_price}/pieza)"
        )

    @admin.action(description="Aprobar presupuestos seleccionados (descuenta stock)")
    def approve_budgets(self, request, queryset):
        for budget in queryset:
            try:
                shortages = budget.approve()
            except BudgetNotApprovableError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue

            if shortages:
                items = ", ".join(s["item"] for s in shortages)
                self.message_user(
                    request,
                    f"Presupuesto #{budget.pk} aprobado con stock insuficiente en: {items}",
                    level=messages.WARNING,
                )
            else:
                self.message_user(
                    request, f"Presupuesto #{budget.pk} aprobado correctamente."
                )
