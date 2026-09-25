from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext, gettext_lazy as _

from config.formatting import format_hours
from production.models import HistorialImpresion, ProductionJob

from .metrics import PERIODS, build_metrics, export_xlsx, template_context
from .models import (
    DisenoNoListoError,
    Metricas,
    Pieza,
    PiezaFilamentLine,
    Presupuesto,
    PresupuestoItem,
    PresupuestoNotApprovableError,
    Producto,
    ProductoAggregateLine,
    StockPiezas,
    StockProductos,
)
from .pdf import (
    presupuesto_pdf_filename,
    render_presupuesto_pdf,
)


# ===========================================================================
#  COSTEO DE PRODUCTOS
# ===========================================================================


class PiezaFilamentLineFormSet(BaseInlineFormSet):
    """Valida la relación entre 'Necesita AMS (multicolor)' y la cantidad de
    líneas de filamento cargadas: con AMS hacen falta 2 o más (varios
    colores); sin AMS no puede haber más de una."""

    def clean(self):
        super().clean()
        lineas_cargadas = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if form.cleaned_data.get("filament"):
                lineas_cargadas += 1

        requires_ams = getattr(self.instance, "requires_ams", False)
        if requires_ams and lineas_cargadas < 2:
            raise ValidationError(
                gettext(
                    "Esta pieza tiene marcado 'Necesita AMS (multicolor)': "
                    "cargá al menos 2 líneas de filamento (una por color)."
                )
            )
        if not requires_ams and lineas_cargadas > 1:
            raise ValidationError(
                gettext(
                    "Para cargar más de una línea de filamento primero tenés "
                    "que marcar 'Necesita AMS (multicolor)'."
                )
            )


class PiezaFilamentLineInline(admin.TabularInline):
    """El costo por gramo casi nunca hay que tocarlo a mano: si se deja
    vacío, se congela solo con el precio actual del filamento (ver
    Filament.cost_per_gram) al guardar. Solo completarlo a mano para forzar
    un precio distinto del que figura en Inventario (ej: un lote comprado
    más caro). La columna "Precio actual del filamento" es de referencia
    (no se guarda), para no confundirse con el campo editable de al lado."""

    model = PiezaFilamentLine
    formset = PiezaFilamentLineFormSet
    extra = 1
    autocomplete_fields = ("filament",)
    readonly_fields = ("precio_actual_display", "line_cost_display")
    fields = (
        "filament",
        "grams_used",
        "precio_actual_display",
        "unit_cost",
        "line_cost_display",
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "unit_cost" and field is not None:
            field.widget.attrs["placeholder"] = gettext(
                "Vacío = precio actual del filamento"
            )
        return field

    @admin.display(description=_("Precio actual del filamento (referencia)"))
    def precio_actual_display(self, obj):
        if not obj or not obj.filament_id:
            return "-"
        return f"${obj.filament.cost_per_gram}/g"

    @admin.display(description=_("Costo de línea (por corrida)"))
    def line_cost_display(self, obj):
        return f"${obj.line_cost}" if obj and obj.pk else "-"


class PiezaInline(admin.TabularInline):
    """Piezas que componen el producto. El filamento de cada pieza se carga
    entrando a la pieza (enlace 'Modificar' que aparece en la fila una vez
    guardada), porque lleva sus propias líneas de filamento."""

    model = Pieza
    extra = 0
    show_change_link = True
    fields = (
        "name",
        "units_needed",
        "pieces_per_gcode",
        "print_time_minutes",
        "requires_ams",
        "stock_quantity",
        "resumen",
    )
    readonly_fields = ("resumen",)

    @admin.display(description=_("Por producto (corridas · gramos · horas)"))
    def resumen(self, obj):
        if not obj.pk:
            return gettext(
                "Guardá el producto para poder cargar el filamento de esta pieza."
            )
        return gettext("%(runs)s corrida/s · %(grams)s g · %(hours)s") % {
            "runs": obj.gcode_runs,
            "grams": obj.filament_grams,
            "hours": format_hours(obj.machine_hours),
        }


@admin.register(Pieza)
class PiezaAdmin(admin.ModelAdmin):
    """Se mantiene registrado para poder buscar/filtrar piezas sueltas y para
    que funcione el enlace 'Modificar' de cada fila en el inline de Piezas
    del producto (PiezaInline, ver ProductoAdmin), pero se saca del menú
    principal del admin: los datos básicos de la pieza se cargan desde
    adentro del producto; el filamento se carga entrando a la pieza."""

    list_display = (
        "name",
        "producto",
        "units_needed",
        "pieces_per_gcode",
        "gcode_runs",
        "requires_ams",
        "stock_quantity",
    )
    list_filter = ("requires_ams", "producto")
    search_fields = ("name", "producto__name")
    autocomplete_fields = ("producto",)
    inlines = (PiezaFilamentLineInline,)
    fields = (
        "producto",
        "name",
        "units_needed",
        "pieces_per_gcode",
        "print_time_minutes",
        "requires_ams",
        "stock_quantity",
        "resumen",
    )
    readonly_fields = ("resumen",)

    def has_module_permission(self, request):
        # Oculta "Piezas" del menú principal: se editan desde adentro del
        # producto (nested admin). Se mantiene registrado por si hace falta
        # acceder por URL directa o para el autocomplete.
        return False

    @admin.display(description=_("Cálculo de la pieza"))
    def resumen(self, obj):
        if not obj.pk:
            return gettext("Guardá la pieza y agregá su filamento para ver el cálculo.")
        ams = gettext("Sí") if obj.requires_ams else gettext("No")
        return mark_safe(
            "<b>" + gettext("Por UN producto:") + "</b><br>"
            "&nbsp;&nbsp;" + gettext("Corridas de gcode:") + f" {obj.gcode_runs} "
            f"(ceil({obj.units_needed} / {obj.pieces_per_gcode}))<br>"
            "&nbsp;&nbsp;" + gettext("Filamento:") + f" {obj.filament_grams} g<br>"
            "&nbsp;&nbsp;" + gettext("Horas de máquina:") + f" {format_hours(obj.machine_hours)}<br>"
            "&nbsp;&nbsp;" + gettext("Costo de filamento:") + f" ${obj.material_cost}<br>"
            "&nbsp;&nbsp;" + gettext("Necesita AMS:") + f" {ams}"
        )


@admin.register(StockPiezas)
class StockPiezasAdmin(admin.ModelAdmin):
    """Stock de piezas ya impresas, por producto. Sube solo cuando un trabajo se
    imprime con sobrante de gcode y baja al aprobar pedidos que las usan. Podés
    ajustar el stock a mano acá si hace falta.

    También sirve como pantalla principal para terminar de cargar una pieza
    después de costear el producto: entrando a una fila (no desde el listado
    editable) se ve el form completo, con el inline de filamento incluido
    (igual que 'Piezas', que queda oculta del menú y solo se usa desde el
    link 'Modificar' del producto)."""

    list_display = ("producto", "name", "requires_ams", "stock_quantity")
    list_filter = ("producto", "requires_ams")
    search_fields = ("name", "producto__name")
    list_editable = ("stock_quantity",)
    ordering = ("producto__name", "order", "id")
    autocomplete_fields = ("producto",)
    inlines = (PiezaFilamentLineInline,)
    fields = (
        "producto",
        "name",
        "units_needed",
        "pieces_per_gcode",
        "print_time_minutes",
        "requires_ams",
        "stock_quantity",
        "resumen",
    )
    readonly_fields = ("resumen",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Cálculo de la pieza"))
    def resumen(self, obj):
        if not obj.pk:
            return gettext("Guardá la pieza y agregá su filamento para ver el cálculo.")
        ams = gettext("Sí") if obj.requires_ams else gettext("No")
        return mark_safe(
            "<b>" + gettext("Por UN producto:") + "</b><br>"
            "&nbsp;&nbsp;" + gettext("Corridas de gcode:") + f" {obj.gcode_runs} "
            f"(ceil({obj.units_needed} / {obj.pieces_per_gcode}))<br>"
            "&nbsp;&nbsp;" + gettext("Filamento:") + f" {obj.filament_grams} g<br>"
            "&nbsp;&nbsp;" + gettext("Horas de máquina:") + f" {format_hours(obj.machine_hours)}<br>"
            "&nbsp;&nbsp;" + gettext("Costo de filamento:") + f" ${obj.material_cost}<br>"
            "&nbsp;&nbsp;" + gettext("Necesita AMS:") + f" {ams}"
        )


@admin.register(StockProductos)
class StockProductosAdmin(admin.ModelAdmin):
    """Stock de productos terminados (armados y listos para entregar sin
    imprimir). Sube al completar un pedido marcado 'para reponer stock' y se
    controla contra el stock mínimo. Podés ajustar el stock a mano acá."""

    list_display = (
        "name",
        "stock_quantity",
        "min_stock",
        "estado_stock",
        "a_reponer",
    )
    list_filter = ("is_active",)
    search_fields = ("name",)
    list_editable = ("stock_quantity", "min_stock")
    ordering = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_active=True)

    @admin.display(description=_("Estado"))
    def estado_stock(self, obj):
        if obj.min_stock <= 0:
            return gettext("Sin mínimo")
        if obj.is_low_stock:
            return mark_safe(
                '<b style="color:#b3261e">⚠ ' + gettext("Bajo mínimo") + "</b>"
            )
        return "OK"

    @admin.display(description=_("Faltan para el mínimo"))
    def a_reponer(self, obj):
        return obj.stock_to_make or "—"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ProductoAggregateLineInline(admin.TabularInline):
    model = ProductoAggregateLine
    extra = 0
    autocomplete_fields = ("aggregate",)
    readonly_fields = ("line_cost_display",)
    fields = ("aggregate", "quantity", "unit_cost", "line_cost_display")

    @admin.display(description=_("Costo de línea"))
    def line_cost_display(self, obj):
        return f"${obj.line_cost}" if obj.pk else "-"


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "priority",
        "diseno_listo",
        "unit_cost_display",
        "unit_price_display",
        "stock_quantity",
        "min_stock",
        "is_multicolor",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "diseno_listo", "is_multicolor", "priority")
    list_editable = ("priority", "diseno_listo")
    search_fields = ("name", "description")
    inlines = (PiezaInline, ProductoAggregateLineInline)
    readonly_fields = ("costs_summary", "price_info", "diseno_listo_at")

    def get_queryset(self, request):
        # unit_cost/unit_price (y el resumen de costos) recorren piezas,
        # líneas de filamento y líneas de agregado por producto. Sin
        # prefetch, cada fila del changelist dispara varias queries extra
        # (N+1) y con catálogos grandes la página se vuelve inutilizable.
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                "piezas__filament_lines__filament",
                "aggregate_lines__aggregate",
            )
        )

    fieldsets = (
        (None, {"fields": ("name", "description", "priority", "is_multicolor", "is_active")}),
        (
            _("Stock de productos terminados"),
            {"fields": ("stock_quantity", "min_stock")},
        ),
        (
            _("Impresión y máquina"),
            {"fields": ("machine_cost_per_hour", "waste_percent")},
        ),
        (
            _("Mano de obra / post-proceso"),
            {"fields": ("post_processing_minutes", "labor_cost_per_hour")},
        ),
        (_("1. Costeo"), {"fields": ("costs_summary",)}),
        (_("2. Precio"), {"fields": ("price_info", "sale_price")}),
        (
            _("Archivo del modelo"),
            {
                "classes": ("collapse",),
                "fields": ("gcode", "model_file"),
            },
        ),
        (
            _("Handoff a producción"),
            {"fields": ("diseno_listo", "diseno_listo_at")},
        ),
    )

    @admin.display(description=_("Costo/pieza"))
    def unit_cost_display(self, obj):
        return f"${obj.unit_cost}"

    @admin.display(description=_("Precio/pieza"))
    def unit_price_display(self, obj):
        return f"${obj.unit_price}"

    @admin.display(description=_("Resumen de costos"))
    def costs_summary(self, obj):
        if not obj.pk:
            return gettext("Guardá el producto para ver el resumen de costos.")
        piezas = obj.piezas.all()
        corridas = gettext("corrida/s")
        piezas_rows = "".join(
            f"&nbsp;&nbsp;&nbsp;&nbsp;{p.name}: {p.gcode_runs} {corridas} · "
            f"{p.filament_grams} g · {format_hours(p.machine_hours)}"
            f"{' · AMS' if p.requires_ams else ''}<br>"
            for p in piezas
        )
        sin_piezas = (
            "&nbsp;&nbsp;&nbsp;&nbsp;" + gettext("(sin piezas todavía)") + "<br>"
        )
        ams = gettext("Sí") if obj.needs_ams else gettext("No")
        return mark_safe(
            "<b>" + gettext("Piezas del producto:") + "</b><br>"
            f"{piezas_rows or sin_piezas}"
            "&nbsp;&nbsp;<b>" + gettext("Total filamento:") + f" {obj.total_filament_grams} g</b><br>"
            "&nbsp;&nbsp;<b>" + gettext("Total horas de máquina:") + f" {format_hours(obj.total_machine_hours)}</b><br>"
            "&nbsp;&nbsp;" + gettext("Necesita AMS (multicolor):") + f" {ams}<br>"
            "<br><b>" + gettext("Costos por producto:") + "</b> "
            "<span style=\"font-weight:normal\">("
            + gettext("imprimiendo esta unidad sola, ahora")
            + ")</span><br>"
            "&nbsp;&nbsp;" + gettext("Material:") + f" ${obj.material_cost} "
            "(+ " + gettext("merma") + f" ${obj.material_waste_cost})<br>"
            "&nbsp;&nbsp;" + gettext("Agregados:") + f" ${obj.aggregate_cost}<br>"
            "&nbsp;&nbsp;" + gettext("Máquina:") + f" ${obj.machine_cost}<br>"
            "&nbsp;&nbsp;" + gettext("Mano de obra:") + f" ${obj.labor_cost}<br>"
            "&nbsp;&nbsp;<b>" + gettext("Costo por producto:") + f" ${obj.unit_cost}</b><br>"
            "<br><b>" + gettext("Costo promedio por producto:") + "</b> "
            "<span style=\"font-weight:normal\">("
            + gettext("si se aprovechan las piezas sobrantes de cada corrida en otros pedidos")
            + ")</span><br>"
            "&nbsp;&nbsp;" + gettext("Material:") + f" ${obj.material_cost_avg} "
            "(+ " + gettext("merma") + f" ${obj.material_waste_cost_avg})<br>"
            "&nbsp;&nbsp;" + gettext("Agregados:") + f" ${obj.aggregate_cost}<br>"
            "&nbsp;&nbsp;" + gettext("Máquina:") + f" ${obj.machine_cost_avg}<br>"
            "&nbsp;&nbsp;" + gettext("Mano de obra:") + f" ${obj.labor_cost}<br>"
            "&nbsp;&nbsp;<b>" + gettext("Costo promedio por producto:") + f" ${obj.unit_cost_avg}</b>"
        )

    @admin.display(description=_("Costo y margen"))
    def price_info(self, obj):
        if not obj.pk:
            return gettext("Guardá el producto para ver el costo y el margen.")
        return mark_safe(
            "&nbsp;&nbsp;" + gettext("Tu costo:") + f" <b>${obj.unit_cost}</b>"
            "&nbsp;&nbsp;|&nbsp;&nbsp;"
            + gettext("Margen con el precio actual:")
            + f" <b>{obj.margin_percent}%</b>"
        )


# ===========================================================================
#  PRESUPUESTOS
# ===========================================================================


class PresupuestoItemInline(admin.TabularInline):
    """El precio unitario NO se carga a mano: se toma siempre del costeo del
    producto (Producto.sale_price) y queda congelado en PresupuestoItem al
    guardar (ver PresupuestoItem.save()). Por eso acá solo se muestran
    columnas de solo lectura; para cambiar el precio de una línea hay que
    ajustar el costeo del producto (o, si cambió después de creado el
    presupuesto, el precio de esta línea queda igual, "congelado")."""

    model = PresupuestoItem
    extra = 1
    autocomplete_fields = ("producto",)
    readonly_fields = (
        "precio_costeo_display",
        "precio_unitario_display",
        "line_total_display",
    )
    fields = (
        "producto",
        "quantity",
        "precio_costeo_display",
        "precio_unitario_display",
        "line_total_display",
    )

    @admin.display(description=_("Precio de costeo (actual)"))
    def precio_costeo_display(self, obj):
        if not obj or not obj.producto_id:
            return "-"
        return f"${obj.producto.unit_price}"

    @admin.display(description=_("Precio unitario (congelado)"))
    def precio_unitario_display(self, obj):
        if not obj or not obj.pk:
            return gettext("Guardá para congelar el precio.")
        return f"${obj.effective_unit_price}"

    @admin.display(description=_("Importe"))
    def line_total_display(self, obj):
        return f"${obj.line_total}" if obj and obj.pk else "-"


class ProductionJobInline(admin.TabularInline):
    model = ProductionJob
    extra = 0
    autocomplete_fields = ("producto", "machine")
    fields = (
        "producto",
        "pieza",
        "quantity",
        "machine",
        "order",
        "status",
        "print_hours_display",
        "estimated_start",
        "estimated_print_end",
    )
    readonly_fields = (
        "pieza",
        "print_hours_display",
        "estimated_start",
        "estimated_print_end",
    )

    @admin.display(description=_("Horas impr."))
    def print_hours_display(self, obj):
        return format_hours(obj.print_hours) if obj.pk else "-"


@admin.register(Presupuesto)
class PresupuestoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "client_name",
        "para_stock",
        "status",
        "listo_display",
        "total_pieces",
        "total_display",
        "due_date_display",
        "pdf_button",
        "created_at",
    )
    list_filter = ("status", "para_stock")
    search_fields = ("client_name", "description")
    list_editable = ("status",)
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
    actions = ("approve_presupuestos", "marcar_entregado", "cancelar_presupuestos")

    fieldsets = (
        (None, {"fields": ("client_name", "para_stock", "description", "status")}),
        (_("Precio del pedido"), {"fields": ("fixed_cost", "totals_summary")}),
        (
            _("Producción y entrega"),
            {
                "fields": (
                    "production_summary",
                    "due_date",
                    "due_date_is_manual",
                )
            },
        ),
        (
            _("Fechas"),
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
        (_("Documento"), {"fields": ("pdf_link",)}),
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
            return HttpResponse(gettext("Presupuesto no encontrado."), status=404)
        pdf_bytes = render_presupuesto_pdf(presupuesto)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{presupuesto_pdf_filename(presupuesto)}"'
        )
        return response

    @admin.display(description=_("PDF"))
    def pdf_button(self, obj):
        url = reverse("admin:budgets_presupuesto_pdf", args=[obj.pk])
        return mark_safe(
            f'<a class="button" href="{url}" target="_blank">'
            + gettext("PDF cliente")
            + "</a>"
        )

    @admin.display(description=_("PDF para el cliente"))
    def pdf_link(self, obj):
        if not obj.pk:
            return gettext("Guardá el presupuesto para poder generar el PDF.")
        url = reverse("admin:budgets_presupuesto_pdf", args=[obj.pk])
        return mark_safe(
            f'<a class="button" href="{url}" target="_blank">'
            + gettext("Descargar PDF para el cliente")
            + "</a>"
        )

    @admin.display(description=_("Sin producción"))
    def listo_display(self, obj):
        # Pedido aprobado que se sirvió 100% del stock: no generó trabajos, así
        # que está listo para entregar y marcar como Completado.
        if obj.is_ready_to_deliver:
            return mark_safe(
                '<b style="color:#15803d;">✓ '
                + gettext("Listo para entregar")
                + "</b>"
            )
        return "—"

    @admin.display(description=_("Total pedido"))
    def total_display(self, obj):
        return f"${obj.total}"

    @admin.display(description=_("Entrega"))
    def due_date_display(self, obj):
        if not obj.due_date:
            return "—"
        fecha = timezone.localtime(obj.due_date).strftime("%d/%m %H:%M")
        return f"{fecha}{' ✋' if obj.due_date_is_manual else ''}"

    @admin.display(description=_("Producción y entrega"))
    def production_summary(self, obj):
        if not obj.pk:
            return gettext("Guardá el presupuesto y aprobalo para generar la cola.")
        jobs = obj.jobs.select_related("producto", "machine").all()
        if not jobs:
            return mark_safe(
                gettext(
                    "Todavía no hay trabajos de producción. Se generan al "
                    "<b>aprobar</b> el presupuesto."
                )
            )
        rows = ""
        for job in jobs:
            maquina = job.machine.name if job.machine else gettext("(sin máquina)")
            fin = (
                timezone.localtime(job.estimated_print_end).strftime("%d/%m %H:%M")
                if job.estimated_print_end
                else "—"
            )
            rows += gettext(
                "&nbsp;&nbsp;%(producto)s ×%(qty)s → <b>%(maquina)s</b> "
                "(%(hours)s, fin impr. %(fin)s)<br>"
            ) % {
                "producto": job.producto,
                "qty": job.quantity,
                "maquina": maquina,
                "hours": format_hours(job.print_hours),
                "fin": fin,
            }
        entrega = (
            timezone.localtime(obj.estimated_delivery).strftime("%d/%m/%Y %H:%M")
            if obj.estimated_delivery
            else "—"
        )
        return mark_safe(
            f"{rows}"
            "&nbsp;&nbsp;" + gettext("Impresión total:") + f" <b>{format_hours(obj.total_print_hours)}</b><br>"
            "&nbsp;&nbsp;" + gettext("Post-proceso total:") + f" <b>{obj.total_post_processing_minutes} min</b><br>"
            "&nbsp;&nbsp;<b>" + gettext("ENTREGA ESTIMADA:") + f" {entrega}</b>"
        )

    def save_model(self, request, obj, form, change):
        # Pedido para stock sin cliente: completamos el nombre solo, así el campo
        # (obligatorio) no molesta y queda claro en los listados.
        if obj.para_stock and not obj.client_name.strip():
            obj.client_name = gettext("Reposición de stock")
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

    def save_formset(self, request, form, formset, change):
        # Trabajos de producción editados desde este inline: el formset los
        # guarda directo (sin pasar por ProductionJobAdmin.save_model), así
        # que si alguno pasó a "Imprimiendo" acá mismo le marcamos el inicio
        # real. Si no, `compute_schedule` no tiene de dónde tomar el inicio y
        # lo recalcula desde "ahora" cada vez que se entra a la cola (las
        # fechas "flotan" en vez de quedar fijas). El resto de los efectos
        # (impreso/cancelado) ya se manejan después en save_related.
        if formset.model is ProductionJob:
            instances = formset.save(commit=False)
            for deleted in formset.deleted_objects:
                deleted.delete()
            for job in instances:
                if job.status == ProductionJob.Status.PRINTING and not job.started_at:
                    job.started_at = timezone.now()
                job.save()
            formset.save_m2m()
        else:
            super().save_formset(request, form, formset, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance

        # Si el estado cambió desde el dropdown, seteamos las fechas y
        # disparamos los efectos (al aprobar: genera la cola de producción).
        old_status = getattr(request, "_old_presupuesto_status", None)
        if old_status is not None and old_status != obj.status:
            # Si el estado salta directo a Aprobado/En producción/Completado
            # sin pasar antes por Aprobado (p. ej. desde el dropdown del
            # listado), igual se va a generar la cola/descontar stock acá
            # abajo (ver Presupuesto.apply_status_change): el chequeo de
            # "diseño listo" tiene que valer también en ese salto, si no se
            # termina produciendo algo sin diseño confirmado.
            dispara_produccion = (
                obj.status
                in (
                    Presupuesto.Status.APPROVED,
                    Presupuesto.Status.IN_PRODUCTION,
                    Presupuesto.Status.COMPLETED,
                )
                and not obj.stock_provisioned
            )
            if dispara_produccion:
                sin_diseno = obj.productos_sin_diseno_listo()
                if sin_diseno:
                    # No se aprueba: revertimos el estado (ya había quedado
                    # guardado por save_model) y avisamos qué falta marcar.
                    obj.status = old_status
                    obj.save(update_fields=["status", "updated_at"])
                    self.message_user(
                        request,
                        gettext(
                            "No se aprobó el presupuesto #%(pk)s: falta marcar "
                            "'Diseño listo' en el costeo de estos productos antes "
                            "de mandarlos a la cola de producción: %(productos)s."
                        )
                        % {"pk": obj.pk, "productos": ", ".join(sin_diseno)},
                        level=messages.ERROR,
                    )
                    return
            result = obj.apply_status_change(old_status)
            if obj.status == Presupuesto.Status.APPROVED:
                self._message_approval(request, obj, result)
            elif dispara_produccion and result is not None:
                # Saltó directo a En producción/Completado: mismo aviso que
                # al aprobar (piezas servidas de stock, faltantes, etc.).
                self._message_approval(request, obj, result)
            elif obj.status == Presupuesto.Status.CANCELLED:
                self._message_cancellation(request, obj, result)

        # Si algún trabajo quedó marcado como "Impreso" desde el inline:
        # el material ya se descontó al aprobar; acá sumamos la sobrante del
        # gcode al stock de la pieza y marcamos el fin real. (Los trabajos del
        # modo anterior sin pieza recién acá descuentan su material.)
        for job in obj.jobs.filter(status=ProductionJob.Status.DONE):
            if not job.finished_at:
                job.finished_at = timezone.now()
                job.save(update_fields=["finished_at"])
            job.consume_stock()  # no-op si ya se descontó al aprobar
            surplus = job.register_surplus()
            if surplus:
                self.message_user(
                    request,
                    gettext(
                        "Trabajo '%(job)s' impreso: %(surplus)s unidad(es) "
                        "sobrante(s) se sumaron al stock de la pieza."
                    )
                    % {"job": job, "surplus": surplus},
                )
            # Recalcula las horas impresas de la máquina y guarda el trabajo en
            # su historial (igual que al marcarlo Impreso desde la cola).
            if job.machine_id:
                job.machine.recalc_printed_hours()
            job.register_history()

        # Si algún trabajo quedó cancelado desde el inline, también lo dejamos
        # registrado en el historial de su máquina (estado Cancelado).
        for job in obj.jobs.filter(status=ProductionJob.Status.CANCELLED):
            job.register_history(estado=HistorialImpresion.Estado.CANCELADO)

        # El estado del presupuesto sigue a la cola de producción: si los
        # trabajos avanzaron (imprimiendo / impresos), el pedido avanza de etapa.
        if obj.sync_status_from_jobs():
            self.message_user(
                request,
                gettext(
                    "El pedido pasó a '%(status)s' según el estado de sus "
                    "trabajos de producción."
                )
                % {"status": obj.get_status_display()},
            )

        # Pedido para stock recién completado: avisamos qué se sumó al stock de
        # productos terminados (el alta ya la hizo el modelo, de forma idempotente).
        if (
            obj.para_stock
            and obj.status == Presupuesto.Status.COMPLETED
            and old_status != Presupuesto.Status.COMPLETED
            and obj.finished_stock_added
        ):
            detalle = ", ".join(
                f"{item.quantity}× {item.producto}"
                for item in obj.items.select_related("producto").all()
                if item.quantity > 0
            )
            if detalle:
                self.message_user(
                    request,
                    gettext(
                        "Pedido de stock #%(pk)s completado: se sumó al stock de "
                        "productos terminados — %(detalle)s."
                    )
                    % {"pk": obj.pk, "detalle": detalle},
                )

        # Tras guardar trabajos/ítems, recalcula cola y entrega estimada.
        if obj.jobs.exists():
            obj.refresh_delivery()

    @admin.display(description=_("Resumen del presupuesto"))
    def totals_summary(self, obj):
        if not obj.pk:
            return gettext("Guardá el presupuesto y agregá productos para ver el total.")
        c_u = gettext("c/u")
        rows = "".join(
            f"&nbsp;&nbsp;{item.producto.name} × {item.quantity} "
            f"(${item.effective_unit_price} {c_u}): ${item.line_total}<br>"
            for item in obj.items.select_related("producto").all()
        )
        sin_productos = "&nbsp;&nbsp;" + gettext("(sin productos todavía)") + "<br>"
        piezas = gettext("%(n)s pieza/s") % {"n": obj.total_pieces}
        return mark_safe(
            f"{rows or sin_productos}"
            "&nbsp;&nbsp;" + gettext("Productos:") + f" ${obj.items_total}<br>"
            "&nbsp;&nbsp;" + gettext("Costo fijo:") + f" ${obj.fixed_cost}<br>"
            "&nbsp;&nbsp;" + gettext("Subtotal:") + f" ${obj.subtotal}<br>"
            "&nbsp;&nbsp;<b>" + gettext("TOTAL:") + f" ${obj.total}</b> "
            f"({piezas})"
        )

    def _message_approval(self, request, obj, result):
        """Avisos al aprobar: piezas que salieron de stock + faltantes de material."""
        result = result or {}
        from_stock = result.get("from_stock") or []
        from_finished = result.get("from_finished") or []
        shortages = result.get("shortages") or []

        if from_finished:
            detalle = ", ".join(
                f'{r["units"]}× {r["producto"]}' for r in from_finished
            )
            self.message_user(
                request,
                gettext(
                    "Presupuesto #%(pk)s: se sirvieron del stock de productos "
                    "terminados (no se vuelven a producir): %(detalle)s."
                )
                % {"pk": obj.pk, "detalle": detalle},
            )

        if from_stock:
            detalle = ", ".join(f'{r["units"]}× {r["pieza"]}' for r in from_stock)
            self.message_user(
                request,
                gettext(
                    "Presupuesto #%(pk)s: se tomaron piezas del stock (no se "
                    "vuelven a imprimir): %(detalle)s."
                )
                % {"pk": obj.pk, "detalle": detalle},
            )

        if shortages:
            items = ", ".join(
                gettext("%(item)s (faltaron %(missing)s)")
                % {"item": s["item"], "missing": s["missing"]}
                for s in shortages
            )
            self.message_user(
                request,
                gettext(
                    "Presupuesto #%(pk)s aprobado y en cola. Ojo: el stock no "
                    "alcanzó al descontar: %(items)s. Quedó en cero y conviene "
                    "reponer."
                )
                % {"pk": obj.pk, "items": items},
                level=messages.WARNING,
            )
        elif obj.is_ready_to_deliver:
            # Se sirvió entero del stock: no hay nada que imprimir.
            self.message_user(
                request,
                gettext(
                    "Presupuesto #%(pk)s aprobado y servido 100%% del stock: NO "
                    "genera producción. Está LISTO PARA ENTREGAR — usá la acción "
                    "«Marcar como entregado» para completarlo."
                )
                % {"pk": obj.pk},
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                gettext(
                    "Presupuesto #%(pk)s aprobado: se descontó el inventario y se "
                    "generó la cola de producción."
                )
                % {"pk": obj.pk},
            )

    @admin.action(description=_("Aprobar presupuesto(s) y generar cola de producción"))
    def approve_presupuestos(self, request, queryset):
        for presupuesto in queryset:
            try:
                result = presupuesto.approve()
            except (PresupuestoNotApprovableError, DisenoNoListoError) as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue
            self._message_approval(request, presupuesto, result)

    @admin.action(description=_("Marcar como entregado (completar pedido listo de stock)"))
    def marcar_entregado(self, request, queryset):
        for presupuesto in queryset:
            if not presupuesto.is_ready_to_deliver:
                self.message_user(
                    request,
                    gettext(
                        "Presupuesto #%(pk)s: no está listo para entregar "
                        "(o tiene producción pendiente, o no está aprobado/servido "
                        "de stock). No se completó."
                    )
                    % {"pk": presupuesto.pk},
                    level=messages.WARNING,
                )
                continue
            old_status = presupuesto.status
            presupuesto.status = Presupuesto.Status.COMPLETED
            presupuesto.apply_status_change(old_status)
            presupuesto.save()
            self.message_user(
                request,
                gettext(
                    "Presupuesto #%(pk)s marcado como entregado y completado."
                )
                % {"pk": presupuesto.pk},
            )

    def _message_cancellation(self, request, obj, result):
        """Avisos al cancelar: qué se devolvió al stock y qué trabajos se cancelaron."""
        result = result or {}
        filaments = result.get("filaments") or []
        aggregates = result.get("aggregates") or []
        piezas = result.get("piezas") or []
        finished = result.get("finished") or []
        jobs_cancelled = result.get("jobs_cancelled") or 0

        if not (filaments or aggregates or piezas or finished or jobs_cancelled):
            self.message_user(
                request,
                gettext(
                    "Presupuesto #%(pk)s cancelado. No había inventario que devolver."
                )
                % {"pk": obj.pk},
            )
            return

        partes = []
        if jobs_cancelled:
            partes.append(
                gettext("%(n)s trabajo(s) cancelado(s)") % {"n": jobs_cancelled}
            )
        if filaments:
            det = ", ".join(f'{f["grams"]} g de {f["item"]}' for f in filaments)
            partes.append(gettext("filamento devuelto: %(det)s") % {"det": det})
        if aggregates:
            det = ", ".join(f'{a["units"]}× {a["item"]}' for a in aggregates)
            partes.append(gettext("agregados devueltos: %(det)s") % {"det": det})
        if finished:
            det = ", ".join(f'{f["units"]}× {f["producto"]}' for f in finished)
            partes.append(
                gettext("productos terminados devueltos: %(det)s") % {"det": det}
            )
        if piezas:
            det = ", ".join(
                f'{p["units"]}× {p["pieza"]} ({p["motivo"]})' for p in piezas
            )
            partes.append(gettext("piezas al stock: %(det)s") % {"det": det})
        self.message_user(
            request,
            gettext("Presupuesto #%(pk)s cancelado. Se revirtió el inventario — ")
            % {"pk": obj.pk}
            + "; ".join(partes)
            + ".",
        )

    @admin.action(description=_("Cancelar presupuesto(s) y devolver el inventario"))
    def cancelar_presupuestos(self, request, queryset):
        for presupuesto in queryset:
            if presupuesto.status == Presupuesto.Status.CANCELLED:
                self.message_user(
                    request,
                    gettext("Presupuesto #%(pk)s ya estaba cancelado.")
                    % {"pk": presupuesto.pk},
                    level=messages.WARNING,
                )
                continue
            result = presupuesto.cancel()
            self._message_cancellation(request, presupuesto, result)


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
            "title": gettext("Métricas"),
            "periods": [
                {"key": k, "label": v["label"], "active": k == period}
                for k, v in PERIODS.items()
            ],
            **template_context(metrics),
            **(extra_context or {}),
        }
        return TemplateResponse(request, self.change_list_template, context)
