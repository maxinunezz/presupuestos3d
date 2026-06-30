from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _


class Maquina(models.Model):
    """
    Una impresora 3D. Define el paralelismo de producción: cada máquina activa
    procesa su propia cola de trabajos.
    """

    name = models.CharField(_("Nombre"), max_length=120, unique=True)
    is_active = models.BooleanField(
        _("Activa"),
        default=True,
        help_text=_("Si está inactiva, no se le asignan trabajos nuevos ni cuenta para la cola."),
    )
    supports_multicolor = models.BooleanField(
        _("Imprime multicolor (AMS)"),
        default=False,
        help_text=_(
            "Marcá si la máquina puede imprimir piezas de varios colores en "
            "simultáneo (ej. Bambu Lab con AMS). La Ender no lo soporta."
        ),
    )
    cost_per_hour = models.DecimalField(
        _("Costo por hora ($/h)"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_(
            "Costo horario de la máquina (amortización, energía, mantenimiento). "
            "Se usa para calcular la depreciación acumulada: horas impresas × este costo."
        ),
    )
    total_hours_printed = models.DecimalField(
        _("Horas impresas (acumuladas)"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_(
            "Horas de impresión de los trabajos ya terminados en esta máquina. "
            "Se recalcula automáticamente al marcar un trabajo como Impreso."
        ),
    )
    notes = models.CharField(_("Notas"), max_length=255, blank=True)
    created_at = models.DateTimeField(_("Creada"), auto_now_add=True)

    class Meta:
        verbose_name = _("Máquina (impresora)")
        verbose_name_plural = _("Máquinas (impresoras)")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def accumulated_depreciation(self) -> Decimal:
        """Depreciación acumulada = horas impresas × costo por hora."""
        return (
            Decimal(self.total_hours_printed or 0) * Decimal(self.cost_per_hour or 0)
        ).quantize(Decimal("0.01"))

    def recalc_printed_hours(self, save=True) -> Decimal:
        """
        Recalcula y guarda las horas impresas acumuladas sumando las horas de
        todos los trabajos ya terminados (DONE) de esta máquina. Idempotente:
        recalcula desde cero, así marcar/desmarcar trabajos nunca desincroniza.
        """
        total = Decimal("0")
        done_jobs = self.jobs.filter(
            status=ProductionJob.Status.DONE
        ).select_related("producto", "pieza")
        for job in done_jobs:
            total += job.print_hours
        self.total_hours_printed = total.quantize(Decimal("0.01"))
        if save:
            Maquina.objects.filter(pk=self.pk).update(
                total_hours_printed=self.total_hours_printed
            )
        return self.total_hours_printed


class ProductionJob(models.Model):
    """
    Un trabajo de impresión: un producto de un presupuesto (con su cantidad),
    asignado a una máquina y con una posición en la cola de esa máquina.

    Un mismo presupuesto puede tener varios trabajos, repartidos en distintas
    máquinas. La unidad de la cola es el producto, no el presupuesto entero.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("En cola")
        PRINTING = "PRINTING", _("Imprimiendo")
        DONE = "DONE", _("Impreso")
        CANCELLED = "CANCELLED", _("Cancelado")

    presupuesto = models.ForeignKey(
        "budgets.Presupuesto",
        verbose_name=_("Presupuesto"),
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    producto = models.ForeignKey(
        "budgets.Producto",
        verbose_name=_("Producto"),
        on_delete=models.PROTECT,
        related_name="jobs",
    )
    pieza = models.ForeignKey(
        "budgets.Pieza",
        verbose_name=_("Pieza"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="jobs",
        help_text=_(
            "Pieza concreta que imprime este trabajo. Si está vacío es un "
            "trabajo a nivel producto (modo anterior)."
        ),
    )
    quantity = models.PositiveIntegerField(
        _("Cantidad de piezas a imprimir"),
        default=1,
        help_text=_("Unidades de la pieza que hay que imprimir para este pedido."),
    )

    machine = models.ForeignKey(
        Maquina,
        verbose_name=_("Máquina"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="jobs",
        help_text=_("Máquina asignada. El sistema recomienda una, podés cambiarla."),
    )
    order = models.PositiveIntegerField(
        _("Orden en la cola"),
        default=0,
        help_text=_("Posición dentro de la cola de la máquina (menor = primero)."),
    )
    status = models.CharField(
        _("Estado"), max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # Snapshot del último cálculo de cola (se recalcula al cambiar la cola).
    estimated_start = models.DateTimeField(_("Inicio estimado"), null=True, blank=True)
    estimated_print_end = models.DateTimeField(
        _("Fin de impresión estimado"), null=True, blank=True
    )

    # Tiempos reales.
    started_at = models.DateTimeField(_("Inicio real"), null=True, blank=True)
    finished_at = models.DateTimeField(_("Fin real"), null=True, blank=True)

    stock_consumed = models.BooleanField(
        _("Material descontado"),
        default=False,
        help_text=_("Se marca cuando se descontó el filamento de este trabajo (al aprobar el pedido)."),
    )
    surplus_added = models.BooleanField(
        _("Sobrante sumado a stock"),
        default=False,
        help_text=_(
            "Se marca cuando, al imprimirse, la sobrante del último gcode se "
            "sumó al stock de la pieza."
        ),
    )

    history_added = models.BooleanField(
        _("Guardado en el historial"),
        default=False,
        help_text=_(
            "Se marca cuando, al imprimirse, el trabajo se guardó en el "
            "historial de la máquina. Evita duplicar el registro."
        ),
    )

    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)

    class Meta:
        verbose_name = _("Trabajo de producción")
        verbose_name_plural = _("Trabajos de producción")
        ordering = ["machine", "producto__priority", "order", "id"]

    def __str__(self):
        nombre = self.pieza.name if self.pieza else str(self.producto)
        return f"{nombre} x{self.quantity} ({self.get_status_display()})"

    def history_title(self) -> str:
        """Título descriptivo de qué se imprimió, para el historial de la máquina."""
        from django.utils.translation import gettext

        if self.pieza:
            nombre = f"{self.pieza.name} ({self.producto})"
        else:
            nombre = str(self.producto)
        cliente = self.presupuesto.client_name or gettext("Reposición de stock")
        return gettext("%(nombre)s ×%(qty)s — Pedido #%(pid)s (%(cliente)s)") % {
            "nombre": nombre,
            "qty": self.quantity,
            "pid": self.presupuesto_id,
            "cliente": cliente,
        }

    def register_history(self, estado=None):
        """
        Guarda un registro en el historial de la máquina que ejecutó el trabajo
        (snapshot con título, cantidad y horas). Se usa al imprimirse (estado
        Impreso) y al cancelarse (estado Cancelado). Idempotente (flag
        history_added). No hace nada si no hay máquina.
        """
        if self.history_added or not self.machine_id:
            return None

        from django.utils import timezone

        if estado is None:
            estado = HistorialImpresion.Estado.IMPRESO

        registro = HistorialImpresion.objects.create(
            maquina_id=self.machine_id,
            presupuesto=self.presupuesto,
            titulo=self.history_title(),
            cantidad=self.quantity,
            horas_impresion=self.print_hours,
            estado=estado,
            finalizado_el=self.finished_at or timezone.now(),
        )
        self.history_added = True
        self.save(update_fields=["history_added"])
        return registro

    @property
    def gcode_runs(self) -> int:
        """Corridas de gcode para imprimir `quantity` unidades de la pieza."""
        if not self.pieza:
            return self.quantity
        import math

        ppg = self.pieza.pieces_per_gcode or 1
        if self.quantity <= 0:
            return 0
        return math.ceil(self.quantity / ppg)

    @property
    def units_printed(self) -> int:
        """Unidades que salen al imprimir (corridas × piezas por gcode)."""
        if not self.pieza:
            return self.quantity
        return self.gcode_runs * (self.pieza.pieces_per_gcode or 1)

    @property
    def surplus_units(self) -> int:
        """Sobrante del último gcode: se imprime de más y va al stock de piezas."""
        return max(self.units_printed - self.quantity, 0)

    @property
    def print_hours(self) -> Decimal:
        """Horas de impresión de este trabajo."""
        if self.pieza:
            # Por pieza: corridas de gcode × horas por corrida.
            return (
                Decimal(self.gcode_runs)
                * Decimal(str(self.pieza.print_time_hours or 0))
            ).quantize(Decimal("0.01"))
        # Modo anterior (sin pieza): cantidad × horas por producto.
        return (
            Decimal(self.quantity) * Decimal(str(self.producto.total_machine_hours or 0))
        ).quantize(Decimal("0.01"))

    @property
    def post_hours(self) -> Decimal:
        """Horas de post-proceso de este trabajo: cantidad × post-proceso por pieza."""
        return (
            Decimal(self.quantity)
            * Decimal(str(self.producto.post_processing_hours or 0))
        ).quantize(Decimal("0.01"))

    @property
    def is_open(self) -> bool:
        """True si el trabajo todavía cuenta para la cola (no terminado ni cancelado)."""
        return self.status in (self.Status.PENDING, self.Status.PRINTING)

    @property
    def requires_multicolor(self) -> bool:
        """True si la pieza necesita una máquina que imprima multicolor (AMS)."""
        if self.pieza:
            return bool(self.pieza.requires_ams)
        if not self.producto:
            return False
        return bool(self.producto.is_multicolor or self.producto.needs_ams)

    def clean(self):
        """Evita asignar una pieza multicolor a una máquina que no lo soporta."""
        from django.core.exceptions import ValidationError
        from django.utils.translation import gettext

        if (
            self.machine_id
            and self.requires_multicolor
            and not self.machine.supports_multicolor
        ):
            nombre = self.pieza.name if self.pieza else str(self.producto)
            raise ValidationError(
                {
                    "machine": gettext(
                        "'%(nombre)s' es multicolor y '%(machine)s' no "
                        "imprime multicolor. Asigná una máquina con AMS."
                    )
                    % {"nombre": nombre, "machine": self.machine}
                }
            )

    def consume_stock(self):
        """
        Descuenta del inventario el material de este trabajo y registra los
        movimientos. Idempotente: solo descuenta una vez (flag stock_consumed).

        Se llama al APROBAR el pedido (no al imprimir), así el consumo impacta
        de inmediato en las métricas de inventario y costos.

          - Trabajo por pieza: descuenta SOLO el filamento de la pieza
            (gramos por corrida × corridas × merma). Los agregados son a nivel
            producto y se descuentan aparte en Presupuesto._provision_production().
          - Trabajo sin pieza (modo anterior): descuenta filamento + agregados
            a nivel producto × cantidad.

        Devuelve la lista de faltantes (insumos cuyo stock no alcanzó).
        """
        if self.stock_consumed:
            return []

        from django.db import transaction
        from django.utils.translation import gettext

        from inventory.models import StockMovement

        shortages = []
        with transaction.atomic():
            if self.pieza:
                waste = self.producto.waste_multiplier
                runs = self.gcode_runs
                for line in self.pieza.filament_lines.select_related("filament").all():
                    grams = (
                        Decimal(str(line.grams_used)) * runs * waste
                    ).quantize(Decimal("0.01"))
                    fil = line.filament
                    # La producción descuenta el consumo completo aunque el
                    # stock quede en negativo: así el faltante queda visible
                    # (pronóstico de compra) y es reversible. Registramos los
                    # gramos completos en el ledger para no desincronizar.
                    shortage = fil.deduct_stock(grams, allow_negative=True)
                    note = gettext("Impresión %(pieza)s (%(producto)s) ×%(quantity)s") % {
                        "pieza": self.pieza.name,
                        "producto": self.producto,
                        "quantity": self.quantity,
                    }
                    if shortage > 0:
                        note += gettext(" (faltaron %(shortage)s g: quedó en negativo)") % {
                            "shortage": shortage
                        }
                        shortages.append({"item": str(fil), "missing": shortage})
                    StockMovement.objects.create(
                        filament=fil,
                        quantity=-grams,
                        reason=StockMovement.Reason.PRODUCTION,
                        related_presupuesto=self.presupuesto,
                        note=note,
                    )
            else:
                producto = self.producto
                for fil, grams_per_product in producto.aggregated_filament():
                    grams = producto.filament_grams_needed(
                        grams_per_product, self.quantity
                    )
                    shortage = fil.deduct_stock(grams, allow_negative=True)
                    note = gettext("Impresión %(producto)s ×%(quantity)s") % {
                        "producto": producto,
                        "quantity": self.quantity,
                    }
                    if shortage > 0:
                        note += gettext(" (faltaron %(shortage)s g: quedó en negativo)") % {
                            "shortage": shortage
                        }
                        shortages.append({"item": str(fil), "missing": shortage})
                    StockMovement.objects.create(
                        filament=fil,
                        quantity=-grams,
                        reason=StockMovement.Reason.PRODUCTION,
                        related_presupuesto=self.presupuesto,
                        note=note,
                    )
                for line in producto.aggregate_lines.select_related("aggregate").all():
                    qty = producto.aggregate_qty_needed(line, self.quantity)
                    agg = line.aggregate
                    shortage = agg.deduct_stock(qty, allow_negative=True)
                    note = gettext("Impresión %(producto)s ×%(quantity)s") % {
                        "producto": producto,
                        "quantity": self.quantity,
                    }
                    if shortage > 0:
                        note += gettext(" (faltaron %(shortage)s: quedó en negativo)") % {
                            "shortage": shortage
                        }
                        shortages.append({"item": str(agg), "missing": shortage})
                    StockMovement.objects.create(
                        aggregate=agg,
                        quantity=-qty,
                        reason=StockMovement.Reason.PRODUCTION,
                        related_presupuesto=self.presupuesto,
                        note=note,
                    )
            self.stock_consumed = True
            self.save(update_fields=["stock_consumed"])
        return shortages

    def job_filament_grams(self):
        """
        Gramos de filamento que consume este trabajo, por línea de filamento de
        la pieza (gramos por corrida × corridas × merma). Devuelve una lista de
        (filament_id, str(filament), grams). Vacío si es un trabajo sin pieza.
        """
        if not self.pieza:
            return []
        waste = self.producto.waste_multiplier
        runs = self.gcode_runs
        lines = []
        for line in self.pieza.filament_lines.select_related("filament").all():
            grams = (Decimal(str(line.grams_used)) * runs * waste).quantize(
                Decimal("0.01")
            )
            lines.append((line.filament_id, str(line.filament), grams))
        return lines

    def mark_obsolete(self, scrap_grams):
        """
        Marca esta impresión como OBSOLETA (salió mal) y la devuelve a la cola
        para reimprimirse.

        El filamento de esta pieza ya se había descontado al aprobar el pedido.
        De esos gramos, `scrap_grams` (lo que físicamente se gastó en la
        impresión fallida) se PIERDEN; la diferencia (total de la pieza −
        scrap_grams) vuelve al stock de filamento. Al volver la pieza a la cola
        se vuelve a marcar como no consumida (`stock_consumed=False`), de modo
        que la reimpresión vuelve a descontar el filamento completo cuando se
        marque como Impresa. Resultado neto: se descuenta el total + el scrap.

        Los AGREGADOS no se tocan: se usan recién en el post-proceso de la pieza
        ya impresa, y como la pieza se reimprime igual, siguen haciendo falta.

        Idempotente respecto del estado: solo opera sobre trabajos por pieza que
        todavía no terminaron (En cola / Imprimiendo) y que tenían su material
        descontado. Devuelve un resumen {"returned": [...], "scrap": Decimal}.
        """
        from django.db import transaction
        from django.utils.translation import gettext

        from inventory.models import Filament, StockMovement

        if not self.pieza_id:
            raise ValueError(
                gettext("Solo se puede marcar obsoleta una impresión por pieza.")
            )
        if self.status not in (self.Status.PENDING, self.Status.PRINTING):
            raise ValueError(
                gettext(
                    "Solo se puede marcar obsoleta una impresión En cola o "
                    "Imprimiendo (todavía no terminada)."
                )
            )
        if not self.stock_consumed:
            raise ValueError(
                gettext(
                    "Esta impresión no tiene material descontado, no hay nada que "
                    "reponer."
                )
            )

        scrap = Decimal(str(scrap_grams or 0))
        if scrap < 0:
            scrap = Decimal("0")

        lines = self.job_filament_grams()
        total_g = sum((g for _, _, g in lines), Decimal("0"))
        # No se puede tirar más de lo que la pieza consume en total.
        if scrap > total_g:
            scrap = total_g

        summary = {"returned": [], "scrap": scrap.quantize(Decimal("0.01"))}
        with transaction.atomic():
            # Prorrateamos el scrap entre las líneas de filamento. Para que la
            # suma de los scrap por línea coincida exactamente con el scrap total
            # (sin descuadres de redondeo en multicolor), la última línea absorbe
            # el residuo en vez de redondear cada una por separado.
            scrap_restante = scrap
            for idx, (fil_id, fil_str, line_g) in enumerate(lines):
                es_ultima = idx == len(lines) - 1
                if total_g <= 0:
                    line_scrap = Decimal("0")
                elif es_ultima:
                    line_scrap = scrap_restante
                else:
                    line_scrap = (scrap * (line_g / total_g)).quantize(
                        Decimal("0.01")
                    )
                    scrap_restante -= line_scrap
                devuelto = (line_g - line_scrap).quantize(Decimal("0.01"))
                if devuelto <= 0:
                    continue
                Filament.objects.filter(pk=fil_id).update(
                    stock_grams=models.F("stock_grams") + devuelto
                )
                StockMovement.objects.create(
                    filament_id=fil_id,
                    quantity=devuelto,
                    reason=StockMovement.Reason.REPRINT_FAILURE,
                    related_presupuesto=self.presupuesto,
                    note=(
                        gettext(
                            "Impresión obsoleta %(pieza)s (%(producto)s): "
                            "se perdieron %(scrap)s g, vuelven %(devuelto)s g al stock. "
                            "Se reimprime."
                        )
                        % {
                            "pieza": self.pieza.name,
                            "producto": self.producto,
                            "scrap": line_scrap,
                            "devuelto": devuelto,
                        }
                    ),
                )
                summary["returned"].append({"item": fil_str, "grams": devuelto})

            # Vuelve a la cola y se marca como no consumida: la reimpresión
            # volverá a descontar el filamento completo al terminar.
            self.status = self.Status.PENDING
            self.stock_consumed = False
            self.started_at = None
            self.finished_at = None
            self.surplus_added = False
            self.history_added = False
            self.save(
                update_fields=[
                    "status",
                    "stock_consumed",
                    "started_at",
                    "finished_at",
                    "surplus_added",
                    "history_added",
                ]
            )
        return summary

    def register_surplus(self):
        """
        Al imprimirse el trabajo, la sobrante del último gcode (lo que se imprime
        de más respecto de lo que pedía el pedido) se suma al stock de la pieza.
        Idempotente (flag surplus_added). Devuelve las unidades sumadas.
        """
        if not self.pieza or self.surplus_added:
            return 0

        from budgets.models import Pieza

        surplus = self.surplus_units
        if surplus > 0:
            Pieza.objects.filter(pk=self.pieza_id).update(
                stock_quantity=models.F("stock_quantity") + surplus
            )
        self.surplus_added = True
        self.save(update_fields=["surplus_added"])
        return surplus


class HistorialImpresion(models.Model):
    """
    Registro histórico (snapshot) de una impresión terminada en una máquina.
    Se crea automáticamente al marcar un trabajo como Impreso. Guarda una copia
    de los datos (título, cantidad, horas) para que el historial sobreviva a
    cambios o borrados del trabajo original.
    """

    class Estado(models.TextChoices):
        IMPRESO = "IMPRESO", _("Impreso")
        CANCELADO = "CANCELADO", _("Cancelado")

    maquina = models.ForeignKey(
        Maquina,
        verbose_name=_("Máquina"),
        on_delete=models.CASCADE,
        related_name="historial",
    )
    presupuesto = models.ForeignKey(
        "budgets.Presupuesto",
        verbose_name=_("Presupuesto"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    titulo = models.CharField(_("Título"), max_length=255)
    cantidad = models.PositiveIntegerField(_("Cantidad"), default=1)
    horas_impresion = models.DecimalField(
        _("Horas de impresión"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
    )
    estado = models.CharField(
        _("Estado"),
        max_length=10,
        choices=Estado.choices,
        default=Estado.IMPRESO,
    )
    finalizado_el = models.DateTimeField(_("Finalizado el"))

    class Meta:
        verbose_name = _("Impresión del historial")
        verbose_name_plural = _("Historial de impresiones")
        ordering = ["-finalizado_el"]

    def __str__(self):
        return self.titulo


class Tablero(ProductionJob):
    """Proxy para tener en el admin el 'Tablero de producción' (panel general)."""

    class Meta:
        proxy = True
        verbose_name = _("Tablero de producción")
        verbose_name_plural = _("Tablero de producción")
