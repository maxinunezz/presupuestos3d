from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.template.response import TemplateResponse
from django.utils.safestring import mark_safe

from .models import (
    Aggregate,
    AjusteStock,
    Compra,
    CompraLine,
    CompraNotConfirmableError,
    Filament,
    StockMovement,
    StockTotals,
)


def _money(value) -> str:
    """Formatea un monto al estilo argentino: 8.000,00"""
    value = Decimal(value or 0).quantize(Decimal("0.01"))
    entero, _, dec = f"{value:.2f}".partition(".")
    negativo = entero.startswith("-")
    entero = entero.lstrip("-")
    partes = []
    while len(entero) > 3:
        partes.insert(0, entero[-3:])
        entero = entero[:-3]
    partes.insert(0, entero)
    signo = "-" if negativo else ""
    return f"{signo}{'.'.join(partes)},{dec}"


def _num(value, decimals=2) -> str:
    """Formatea un número (kg, cantidades) al estilo argentino."""
    value = Decimal(value or 0).quantize(Decimal("1." + "0" * decimals))
    entero, _, dec = f"{value:.{decimals}f}".partition(".")
    partes = []
    while len(entero) > 3:
        partes.insert(0, entero[-3:])
        entero = entero[:-3]
    partes.insert(0, entero)
    return f"{'.'.join(partes)},{dec}"


class LowStockFilter(admin.SimpleListFilter):
    """Filtra los artículos cuyo stock está por debajo de su mínimo (>0)."""

    title = "Bajo stock"
    parameter_name = "low_stock"

    # Nombre del campo de stock según el modelo (se setea en subclases).
    stock_field = None

    def lookups(self, request, model_admin):
        return (
            ("yes", "Sí (por debajo del mínimo)"),
            ("no", "No (stock OK)"),
        )

    def queryset(self, request, queryset):
        low = Q(min_stock__gt=0) & Q(**{f"{self.stock_field}__lt": F("min_stock")})
        if self.value() == "yes":
            return queryset.filter(low)
        if self.value() == "no":
            return queryset.exclude(low)
        return queryset


class FilamentLowStockFilter(LowStockFilter):
    stock_field = "stock_grams"


class AggregateLowStockFilter(LowStockFilter):
    stock_field = "stock_quantity"


def _stock_badge(is_low):
    """Devuelve un badge HTML rojo/verde según el estado de stock."""
    if is_low:
        return mark_safe(
            '<span style="color:#fff;background:#dc3545;border-radius:3px;'
            'padding:2px 7px;font-weight:bold;">&#9888; Bajo</span>'
        )
    return mark_safe(
        '<span style="color:#fff;background:#28a745;border-radius:3px;'
        'padding:2px 7px;">OK</span>'
    )


@admin.register(Filament)
class FilamentAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "material_type",
        "color",
        "cost_per_kg",
        "cost_per_gram",
        "stock_grams",
        "min_stock",
        "stock_status",
        "is_active",
    )
    list_filter = (FilamentLowStockFilter, "material_type", "brand", "is_active")
    search_fields = ("brand", "color")
    list_editable = ("cost_per_kg", "min_stock", "is_active")
    # El stock NO se edita a mano: solo cambia al confirmar una Compra. Así un
    # filamento creado desde el botón "+" de una compra arranca en 0 y recién
    # suma cuando la compra pasa a "Confirmada".
    readonly_fields = ("stock_grams",)

    @admin.display(description="Costo/g")
    def cost_per_gram(self, obj):
        return f"${obj.cost_per_gram}"

    @admin.display(description="Estado stock")
    def stock_status(self, obj):
        return _stock_badge(obj.is_low_stock)


@admin.register(Aggregate)
class AggregateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "unit",
        "cost_per_unit",
        "stock_quantity",
        "min_stock",
        "stock_status",
        "is_active",
    )
    list_filter = (AggregateLowStockFilter, "category", "is_active")
    search_fields = ("name",)
    list_editable = ("cost_per_unit", "min_stock", "is_active")
    # El stock NO se edita a mano: solo cambia al confirmar una Compra.
    readonly_fields = ("stock_quantity",)

    @admin.display(description="Estado stock")
    def stock_status(self, obj):
        return _stock_badge(obj.is_low_stock)


@admin.register(StockTotals)
class StockTotalsAdmin(admin.ModelAdmin):
    """
    Página de solo lectura con totales de inventario. Buscador único que
    filtra filamentos y agregados a la vez, mostrando detalle y totales
    de peso/cantidad y dinero.
    """

    change_list_template = "admin/inventory/stock_totals.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        q = request.GET.get("q", "").strip()
        only = request.GET.get("only", "").strip()  # "", "filament" o "aggregate"

        filaments = Filament.objects.all()
        aggregates = Aggregate.objects.all()

        if q:
            filaments = filaments.filter(
                Q(brand__icontains=q)
                | Q(color__icontains=q)
                | Q(material_type__icontains=q)
            )
            aggregates = aggregates.filter(
                Q(name__icontains=q) | Q(category__icontains=q)
            )

        show_fil = only in ("", "filament")
        show_agg = only in ("", "aggregate")

        fil_rows = []
        fil_total_grams = Decimal("0")
        fil_total_money = Decimal("0")
        if show_fil:
            for f in filaments:
                value = (f.stock_grams * f.cost_per_gram).quantize(Decimal("0.01"))
                fil_total_grams += f.stock_grams
                fil_total_money += value
                fil_rows.append(
                    {
                        "name": str(f),
                        "brand": f.brand,
                        "stock_grams": _num(f.stock_grams),
                        "stock_kg": _num(f.stock_grams / Decimal("1000"), 3),
                        "cost_per_kg": _money(f.cost_per_kg),
                        "value": _money(value),
                    }
                )

        agg_rows = []
        agg_total_qty = Decimal("0")
        agg_total_money = Decimal("0")
        if show_agg:
            for a in aggregates:
                value = (a.stock_quantity * a.cost_per_unit).quantize(Decimal("0.01"))
                agg_total_qty += a.stock_quantity
                agg_total_money += value
                agg_rows.append(
                    {
                        "name": a.name,
                        "category": a.get_category_display(),
                        "unit": a.get_unit_display(),
                        "stock_quantity": _num(a.stock_quantity),
                        "cost_per_unit": _money(a.cost_per_unit),
                        "value": _money(value),
                    }
                )

        context = {
            **self.admin_site.each_context(request),
            "title": "Totales de inventario",
            "query": q,
            "only": only,
            "fil_rows": fil_rows,
            "agg_rows": agg_rows,
            "fil_total_grams": _num(fil_total_grams),
            "fil_total_kg": _num(fil_total_grams / Decimal("1000"), 3),
            "fil_total_money": _money(fil_total_money),
            "agg_total_qty": _num(agg_total_qty),
            "agg_total_money": _money(agg_total_money),
            "grand_total_money": _money(fil_total_money + agg_total_money),
            "show_fil": show_fil,
            "show_agg": show_agg,
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)


class CompraLineInline(admin.TabularInline):
    model = CompraLine
    extra = 1
    autocomplete_fields = ("filament", "aggregate")
    readonly_fields = ("line_cost_display",)
    fields = ("filament", "aggregate", "quantity", "unit_price", "line_cost_display")

    @admin.display(description="Costo de línea")
    def line_cost_display(self, obj):
        return f"${obj.line_cost}" if obj.pk else "-"

    def get_readonly_fields(self, request, obj=None):
        # Una compra confirmada no se puede editar (ya impactó el inventario).
        if obj and obj.status == Compra.Status.CONFIRMED:
            return ("filament", "aggregate", "quantity", "unit_price", "line_cost_display")
        return self.readonly_fields


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "supplier",
        "status",
        "total_display",
        "created_at",
        "confirmed_at",
    )
    list_filter = ("status",)
    search_fields = ("supplier", "invoice_number", "notes")
    inlines = (CompraLineInline,)
    readonly_fields = ("confirmed_at", "totals_summary")
    actions = ("confirm_compras",)

    fieldsets = (
        (None, {"fields": ("supplier", "invoice_number", "notes", "status")}),
        ("Resumen", {"fields": ("totals_summary", "confirmed_at")}),
    )

    def save_model(self, request, obj, form, change):
        # El impacto al inventario NUNCA ocurre mientras la compra está en
        # Borrador. Si el usuario eligió "Confirmada" en el desplegable de
        # estado, detectamos la transición acá pero NO marcamos la compra como
        # confirmada todavía: la dejamos en Borrador y disparamos confirm() en
        # save_related(), cuando las líneas ya están guardadas. Así el stock se
        # suma una sola vez y solo al confirmar.
        obj._confirm_on_save = False
        previous_status = (
            Compra.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
            if obj.pk
            else None
        )
        if (
            obj.status == Compra.Status.CONFIRMED
            and previous_status != Compra.Status.CONFIRMED
        ):
            obj._confirm_on_save = True
            obj.status = Compra.Status.DRAFT
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        if getattr(obj, "_confirm_on_save", False):
            try:
                obj.confirm()
            except CompraNotConfirmableError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(
                    request,
                    f"Compra #{obj.pk} confirmada: stock y precios actualizados.",
                )

    @admin.display(description="Total")
    def total_display(self, obj):
        return f"$ {_money(obj.total)}"

    @admin.display(description="Resumen de la compra")
    def totals_summary(self, obj):
        from django.utils.safestring import mark_safe

        if not obj.pk:
            return "Guardá la compra y agregá líneas para ver el total."
        rows = "".join(
            f"&nbsp;&nbsp;{line.item} × {line.quantity}"
            f"{'g' if line.filament_id else ''} "
            f"(${line.effective_unit_price}{'/kg' if line.filament_id else '/u'}): "
            f"$ {_money(line.line_cost)}<br>"
            for line in obj.lines.all()
        )
        estado = ""
        if obj.status == Compra.Status.DRAFT:
            estado = (
                "<br><i>Borrador: todavía no impactó el inventario. Usá la acción "
                "“Confirmar compra” en la lista para sumar el stock.</i>"
            )
        return mark_safe(
            f"{rows or '&nbsp;&nbsp;(sin líneas todavía)<br>'}"
            f"&nbsp;&nbsp;<b>TOTAL: $ {_money(obj.total)}</b>{estado}"
        )

    @admin.action(description="Confirmar compra(s) seleccionadas (suma stock)")
    def confirm_compras(self, request, queryset):
        for compra in queryset:
            try:
                compra.confirm()
            except CompraNotConfirmableError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue
            self.message_user(
                request,
                f"Compra #{compra.pk} confirmada: stock y precios actualizados.",
            )


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "item",
        "quantity",
        "reason",
        "origen",
        "note",
    )
    list_filter = ("reason",)
    readonly_fields = ("created_at",)

    @admin.display(description="Ítem")
    def item(self, obj):
        return obj.filament or obj.aggregate

    @admin.display(description="Origen")
    def origen(self, obj):
        return obj.related_presupuesto or "—"


class AjusteStockForm(forms.ModelForm):
    class Meta:
        model = AjusteStock
        fields = ("filament", "aggregate", "quantity", "note")
        help_texts = {
            "filament": "Elegí el filamento a ajustar (o un agregado, no ambos).",
            "aggregate": "Elegí el agregado a ajustar (o un filamento, no ambos).",
            "quantity": (
                "Cantidad a ajustar. POSITIVO suma al stock, NEGATIVO resta. "
                "Filamento en gramos, agregado en unidades. "
                "Ej: 500 agrega 500 g; -200 quita 200 g. "
                "Si querés dejar el stock en un valor exacto, fijate cuánto hay "
                "hoy y poné la diferencia."
            ),
            "note": (
                "Motivo del ajuste (ej: conteo físico, rotura, sobrante de "
                "impresión, carga inicial de stock)."
            ),
        }

    def clean(self):
        cleaned = super().clean()
        filament = cleaned.get("filament")
        aggregate = cleaned.get("aggregate")
        if bool(filament) == bool(aggregate):
            raise ValidationError(
                "Elegí exactamente un artículo: un Filamento o un Agregado "
                "(no ambos, no ninguno)."
            )
        if not cleaned.get("quantity"):
            raise ValidationError("Ingresá una cantidad distinta de cero.")
        return cleaned


@admin.register(AjusteStock)
class AjusteStockAdmin(admin.ModelAdmin):
    """
    Ajuste manual de stock. Cada alta aplica la diferencia al stock del
    artículo y queda registrada como un movimiento (motivo Ajuste manual).
    Es solo de alta: los ajustes no se editan ni se borran, para no
    descuadrar el stock contra el historial.
    """

    form = AjusteStockForm
    autocomplete_fields = ("filament", "aggregate")
    list_display = ("created_at", "item", "quantity", "stock_resultante", "note")
    readonly_fields = ("created_at",)

    @admin.display(description="Ítem")
    def item(self, obj):
        return obj.filament or obj.aggregate

    @admin.display(description="Stock luego del ajuste")
    def stock_resultante(self, obj):
        if obj.filament_id:
            return f"{_num(obj.filament.stock_grams)} g"
        if obj.aggregate_id:
            return _num(obj.aggregate.stock_quantity)
        return "—"

    def get_queryset(self, request):
        # Esta sección lista solo los ajustes manuales, no los demás movimientos.
        return super().get_queryset(request).filter(
            reason=StockMovement.Reason.MANUAL_ADJUSTMENT
        )

    def has_change_permission(self, request, obj=None):
        # Solo alta + consulta en lista: un ajuste no se re-edita (evita
        # re-aplicar la diferencia y descuadrar el stock).
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        # Motivo fijo y aplicación de la diferencia al stock del artículo,
        # cappeada en 0 (el stock nunca queda negativo).
        obj.reason = StockMovement.Reason.MANUAL_ADJUSTMENT
        delta = obj.quantity
        if obj.filament_id:
            fil = obj.filament
            fil.stock_grams = max(Decimal("0"), fil.stock_grams + delta)
            fil.save(update_fields=["stock_grams", "updated_at"])
            resultante = fil.stock_grams
            unidad = "g"
        else:
            agg = obj.aggregate
            agg.stock_quantity = max(Decimal("0"), agg.stock_quantity + delta)
            agg.save(update_fields=["stock_quantity", "updated_at"])
            resultante = agg.stock_quantity
            unidad = "u"
        super().save_model(request, obj, form, change)
        self.message_user(
            request,
            f"Stock ajustado en {delta}{unidad}. Nuevo stock: "
            f"{_num(resultante)} {unidad}.",
        )
