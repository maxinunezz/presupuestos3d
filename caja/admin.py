from datetime import datetime

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from budgets.metrics import PERIODS

from .metrics import build_caja, caja_template_context, export_caja_xlsx
from .models import CuentaCaja, MovimientoCaja, PanelCaja


@admin.register(CuentaCaja)
class CuentaCajaAdmin(admin.ModelAdmin):
    """
    Cuentas de caja reales (Efectivo, Banco, Mercado Pago, Ualá...). CRUD
    normal, como `Socio` en budgets: el dueño las da de alta y edita él
    mismo, sin tocar programación.
    """

    list_display = (
        "nombre",
        "saldo_inicial",
        "saldo_actual_display",
        "medio_pago_default",
        "is_active",
        "order",
    )
    list_editable = ("order", "is_active")
    ordering = ("order", "nombre")

    @admin.display(description=_("Saldo actual"))
    def saldo_actual_display(self, obj):
        return f"$ {obj.saldo_actual:,.2f}"


@admin.register(MovimientoCaja)
class MovimientoCajaAdmin(admin.ModelAdmin):
    """
    Libro diario de caja. CRUD libre (sin restricciones de borrado/edición:
    el dueño es el único usuario del sistema) y a la vez bandeja de revisión
    de los borradores automáticos generados por `Presupuesto`/`Gasto` al
    guardarse con un medio de pago de caja inmediata.
    """

    list_display = (
        "fecha",
        "cuenta",
        "tipo",
        "monto",
        "concepto",
        "estado",
        "origen_automatico",
    )
    list_filter = ("cuenta", "tipo", "estado", "origen_automatico")
    list_editable = ("cuenta", "estado")
    search_fields = ("concepto",)
    date_hierarchy = "fecha"
    ordering = ("-fecha", "-pk")
    actions = ("confirmar_seleccionados", "descartar_seleccionados")

    @admin.action(description=_("Confirmar seleccionados"))
    def confirmar_seleccionados(self, request, queryset):
        actualizados = 0
        for mov in queryset:
            mov.estado = MovimientoCaja.Estado.CONFIRMADO
            try:
                mov.full_clean()
            except ValidationError as exc:
                self.message_user(
                    request,
                    gettext("Movimiento #%(pk)s: %(error)s")
                    % {"pk": mov.pk, "error": "; ".join(exc.messages)},
                    level=messages.ERROR,
                )
                continue
            mov.save(update_fields=["estado"])
            actualizados += 1
        if actualizados:
            self.message_user(
                request,
                gettext("%(n)s movimiento(s) confirmado(s).") % {"n": actualizados},
            )

    @admin.action(description=_("Descartar seleccionados"))
    def descartar_seleccionados(self, request, queryset):
        n = queryset.update(estado=MovimientoCaja.Estado.DESCARTADO)
        self.message_user(
            request, gettext("%(n)s movimiento(s) descartado(s).") % {"n": n}
        )


@admin.register(PanelCaja)
class PanelCajaAdmin(admin.ModelAdmin):
    """
    Panel de solo lectura: libro diario de caja real (ingresos/egresos
    CONFIRMADOS del período + saldo actual por cuenta + movimientos
    pendientes de revisar), a diferencia del devengado del Panel de
    métricas. Standalone (mismo patrón que `TableroAdmin`/`MetricasAdmin`),
    con su propio motor en `caja.metrics`. Por ahora solo exporta a Excel
    (sin PDF).
    """

    change_list_template = "admin/caja/panel_caja.html"
    dashboard_title = _("Panel de caja")

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

        selected_month = None
        if period == "month":
            date_param = request.GET.get("date", "")
            try:
                anio_str, mes_str = date_param.split("-")
                anio, mes = int(anio_str), int(mes_str)
                if 1 <= mes <= 12 and 2000 <= anio <= 2100:
                    selected_month = timezone.make_aware(
                        datetime(anio, mes, 1), timezone.get_current_timezone()
                    )
            except (ValueError, AttributeError):
                selected_month = None

        data = build_caja(period, now=selected_month)

        if request.GET.get("export") == "xlsx":
            fname, content = export_caja_xlsx(data)
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
            "title": self.dashboard_title,
            "periods": [
                {"key": k, "label": v["label"], "active": k == period}
                for k, v in PERIODS.items()
            ],
            **caja_template_context(data),
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)
