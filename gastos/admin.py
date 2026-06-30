from django.contrib import admin
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from .metrics import (
    _meses,
    available_years,
    build_gastos_metrics,
    export_xlsx,
    template_context,
)
from .models import Gasto, PanelGastos, TopeGasto


@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    list_display = (
        "fecha",
        "categoria",
        "concepto",
        "monto_display",
        "es_recurrente",
        "periodicidad",
        "proveedor",
        "medio_pago",
    )
    list_filter = ("categoria", "es_recurrente", "periodicidad", "medio_pago")
    search_fields = ("concepto", "proveedor", "notas")
    date_hierarchy = "fecha"
    fieldsets = (
        (None, {"fields": ("categoria", "concepto", "monto", "fecha")}),
        (_("Pago"), {"fields": ("proveedor", "medio_pago")}),
        (
            _("Recurrencia"),
            {
                "fields": ("es_recurrente", "periodicidad"),
                "description": _(
                    "Marcá los gastos fijos (suscripciones, abonos) para que entren "
                    "en el compromiso mensual del panel."
                ),
            },
        ),
        (_("Notas"), {"fields": ("notas",)}),
    )

    @admin.display(description=_("Monto"), ordering="monto")
    def monto_display(self, obj):
        from budgets.pdf import format_money

        return f"$ {format_money(obj.monto)}"


@admin.register(TopeGasto)
class TopeGastoAdmin(admin.ModelAdmin):
    list_display = ("categoria", "monto_mensual")
    list_editable = ("monto_mensual",)


@admin.register(PanelGastos)
class PanelGastosAdmin(admin.ModelAdmin):
    """
    Página de solo lectura con el resumen de gastos por mes/año: total, desglose
    por categoría, evolución, comparativo con el período anterior, compromiso
    mensual recurrente, resultado operativo vs ventas y control de topes.
    """

    change_list_template = "admin/gastos/panel.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        today = timezone.localdate()
        years = available_years()

        try:
            year = int(request.GET.get("year", today.year))
        except (TypeError, ValueError):
            year = today.year
        if year not in years:
            year = today.year

        raw_month = request.GET.get("month")
        if raw_month in (None, "", "0"):
            month = today.month if raw_month is None else 0
        else:
            try:
                month = int(raw_month)
            except (TypeError, ValueError):
                month = today.month
            if month not in range(1, 13):
                month = 0

        metrics = build_gastos_metrics(year, month or None)

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

        meses = _meses()
        meses_opts = [{"value": 0, "label": gettext("Todo el año"), "active": month == 0}]
        meses_opts += [
            {"value": i, "label": meses[i], "active": month == i}
            for i in range(1, 13)
        ]
        years_opts = [{"value": y, "active": y == year} for y in years]

        context = {
            **self.admin_site.each_context(request),
            "title": gettext("Panel de gastos"),
            "years_opts": years_opts,
            "meses_opts": meses_opts,
            "sel_year": year,
            "sel_month": month,
            **template_context(metrics),
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)
