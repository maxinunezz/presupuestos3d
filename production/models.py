from decimal import Decimal

from django.db import models
from django.utils import timezone
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
        Recalcula y guarda las horas impresas acumuladas de esta máquina.

        Suma las horas de cada TRAMO (segmento por máquina) de los trabajos ya
        terminados (DONE) que se imprimieron, aunque sea parcialmente, en esta
        máquina — así un trabajo que empezó en una máquina y terminó en otra
        reparte sus horas entre ambas según cuántas corridas hizo cada una.
        Los trabajos terminados que nunca pasaron por el sistema de tramos
        (impresiones de antes de esta función) se siguen contando enteros en
        su máquina final, como antes. Idempotente: recalcula desde cero.
        """
        total = Decimal("0")
        tramos = TramoImpresion.objects.filter(
            machine=self, job__status=ProductionJob.Status.DONE
        ).select_related("job", "job__producto", "job__pieza")
        for tramo in tramos:
            job = tramo.job
            runs_total = job.gcode_runs or 1
            total += job.print_hours * Decimal(tramo.runs) / Decimal(runs_total)

        tramo_job_ids = set(
            TramoImpresion.objects.filter(job__status=ProductionJob.Status.DONE)
            .values_list("job_id", flat=True)
            .distinct()
        )
        legacy_done = self.jobs.filter(status=ProductionJob.Status.DONE).exclude(
            pk__in=tramo_job_ids
        ).select_related("producto", "pieza")
        for job in legacy_done:
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
    completed_runs = models.PositiveIntegerField(
        _("Corridas de gcode terminadas"),
        default=0,
        help_text=_(
            "Cuántas corridas de gcode de este trabajo ya se imprimieron. Se "
            "actualiza con el botón \"Corrida terminada\" de la cola/tablero; "
            "al llegar al total de corridas necesarias el trabajo pasa a Impreso."
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
        Guarda uno o más registros en el historial de las máquinas que
        ejecutaron el trabajo (snapshot con título, cantidad y horas). Se usa
        al imprimirse (estado Impreso) y al cancelarse (estado Cancelado).
        Idempotente (flag history_added).

        Si el trabajo tiene TRAMOS (imprimió, aunque sea parcialmente, en más
        de una máquina), genera un registro POR TRAMO, repartiendo la
        cantidad y las horas del trabajo proporcionalmente a las corridas que
        hizo cada máquina (la última absorbe el resto, para que la suma
        cierre exacto). Si no tiene tramos (trabajos viejos, o cancelados sin
        haber arrancado), mantiene el comportamiento anterior: un solo
        registro en la máquina actual del trabajo.
        """
        if self.history_added:
            return []

        from decimal import ROUND_HALF_UP

        from django.utils import timezone

        if estado is None:
            estado = HistorialImpresion.Estado.IMPRESO

        finalizado = self.finished_at or timezone.now()
        tramos = [t for t in self.tramos.all().order_by("started_at") if t.runs > 0]
        registros = []

        if tramos:
            total_runs = sum(t.runs for t in tramos)
            qty_restante = self.quantity
            horas_restante = self.print_hours
            for idx, tramo in enumerate(tramos):
                es_ultimo = idx == len(tramos) - 1
                if es_ultimo:
                    cantidad = qty_restante
                    horas = horas_restante
                else:
                    frac = Decimal(tramo.runs) / Decimal(total_runs)
                    cantidad = int(
                        (Decimal(self.quantity) * frac).to_integral_value(
                            rounding=ROUND_HALF_UP
                        )
                    )
                    cantidad = min(cantidad, qty_restante)
                    horas = (self.print_hours * frac).quantize(Decimal("0.01"))
                    horas = min(horas, horas_restante)
                qty_restante -= cantidad
                horas_restante -= horas
                if cantidad <= 0 and horas <= 0:
                    continue
                registros.append(
                    HistorialImpresion.objects.create(
                        maquina_id=tramo.machine_id,
                        presupuesto=self.presupuesto,
                        titulo=self.history_title(),
                        cantidad=max(cantidad, 0),
                        horas_impresion=max(horas, Decimal("0")),
                        estado=estado,
                        finalizado_el=finalizado,
                    )
                )
        elif self.machine_id:
            registros.append(
                HistorialImpresion.objects.create(
                    maquina_id=self.machine_id,
                    presupuesto=self.presupuesto,
                    titulo=self.history_title(),
                    cantidad=self.quantity,
                    horas_impresion=self.print_hours,
                    estado=estado,
                    finalizado_el=finalizado,
                )
            )
        else:
            return []

        self.history_added = True
        self.save(update_fields=["history_added"])
        return registros

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

    def advance_run(self) -> int:
        """
        Suma una corrida de gcode terminada (tope: `gcode_runs`). Además
        acredita esa corrida al TRAMO abierto de la máquina actual (ver
        `_current_tramo`), así queda registrado en qué máquina se hizo cada
        corrida. No guarda el estado del trabajo ni dispara los efectos de
        fin (eso lo hace quien llama, vía `ProductionJobAdmin.save_model`,
        para que se comporte igual que marcar el trabajo Impreso a mano).
        Devuelve `completed_runs`.
        """
        total = self.gcode_runs
        if self.completed_runs < total:
            self.completed_runs += 1
            tramo = self._current_tramo()
            if tramo:
                tramo.runs += 1
                tramo.save(update_fields=["runs"])
        return self.completed_runs

    def _current_tramo(self):
        """
        Devuelve el tramo (segmento por máquina) abierto de este trabajo en
        su máquina ACTUAL, creándolo si hace falta. Si había un tramo abierto
        en otra máquina (porque se reasignó el trabajo), lo cierra acá mismo:
        así el cambio de máquina se "autocorrige" solo, sin necesidad de un
        botón especial que lo dispare — se detecta la próxima vez que se
        registra una corrida o que el trabajo termina.
        """
        if not self.machine_id:
            return None
        from django.utils import timezone

        abierto = self.tramos.filter(is_open=True).order_by("-started_at").first()
        if abierto and abierto.machine_id == self.machine_id:
            return abierto
        if abierto:
            abierto.is_open = False
            abierto.closed_at = timezone.now()
            abierto.save(update_fields=["is_open", "closed_at"])
        return self.tramos.create(machine_id=self.machine_id)

    def finalize_tramos(self):
        """
        Al terminar el trabajo (Impreso), completa el tramo de la máquina
        actual con las corridas que todavía no se hubieran contabilizado (por
        ejemplo si se marcó Impreso directo, sin ir apretando "Corrida
        terminada" una por una), y cierra cualquier tramo que hubiera quedado
        abierto. Así la suma de corridas de los tramos siempre coincide con
        `gcode_runs`, y `register_history`/`Maquina.recalc_printed_hours`
        reparten bien las horas entre las máquinas que participaron.
        """
        from django.utils import timezone

        total = self.gcode_runs
        logged = sum(self.tramos.values_list("runs", flat=True))
        remaining = max(total - logged, 0)
        now = timezone.now()

        if remaining and self.machine_id:
            tramo = self.tramos.filter(
                is_open=True, machine_id=self.machine_id
            ).first()
            if tramo:
                tramo.runs += remaining
                tramo.save(update_fields=["runs"])
            else:
                self.tramos.create(machine_id=self.machine_id, runs=remaining)

        self.tramos.filter(is_open=True).update(is_open=False, closed_at=now)

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
            # Por pieza: corridas de gcode × horas por corrida (el campo está en minutos).
            minutes_per_run = Decimal(str(self.pieza.print_time_minutes or 0))
            return (
                Decimal(self.gcode_runs) * (minutes_per_run / Decimal("60"))
            ).quantize(Decimal("0.01"))
        # Modo anterior (sin pieza): cantidad × horas por producto.
        return (
            Decimal(self.quantity) * Decimal(str(self.producto.total_machine_hours or 0))
        ).quantize(Decimal("0.01"))

    @property
    def post_hours(self) -> Decimal:
        """Horas de post-proceso de este trabajo: cantidad × post-proceso por pieza."""
        minutes = Decimal(self.quantity) * Decimal(
            str(self.producto.post_processing_minutes or 0)
        )
        return (minutes / Decimal("60")).quantize(Decimal("0.01"))

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
            self.completed_runs = 0
            # La impresión fallida se descarta entera: los tramos que se
            # hubieran registrado (qué máquina hizo qué corridas) ya no
            # sirven, la reimpresión arranca de cero.
            self.tramos.all().delete()
            self.save(
                update_fields=[
                    "status",
                    "stock_consumed",
                    "started_at",
                    "finished_at",
                    "surplus_added",
                    "history_added",
                    "completed_runs",
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


class TramoImpresion(models.Model):
    """
    Un TRAMO es el segmento de corridas de gcode de un trabajo que se
    imprimieron en una máquina en particular. La mayoría de los trabajos
    tienen un solo tramo (empezaron y terminaron en la misma máquina). Si el
    trabajo se reasigna a otra máquina mientras imprime, se cierra el tramo
    viejo y se abre uno nuevo: así queda registrado cuántas corridas hizo
    cada máquina, y `Maquina.recalc_printed_hours()` / `HistorialImpresion`
    reparten las horas y el historial correctamente entre ambas en vez de
    atribuirle todo a la máquina final (ver `ProductionJob._current_tramo`).
    """

    job = models.ForeignKey(
        ProductionJob,
        verbose_name=_("Trabajo"),
        on_delete=models.CASCADE,
        related_name="tramos",
    )
    machine = models.ForeignKey(
        Maquina,
        verbose_name=_("Máquina"),
        on_delete=models.CASCADE,
        related_name="tramos",
    )
    runs = models.PositiveIntegerField(_("Corridas de gcode"), default=0)
    is_open = models.BooleanField(_("Abierto"), default=True)
    started_at = models.DateTimeField(_("Inicio"), auto_now_add=True)
    closed_at = models.DateTimeField(_("Cierre"), null=True, blank=True)

    class Meta:
        verbose_name = _("Tramo de impresión")
        verbose_name_plural = _("Tramos de impresión")
        ordering = ["job", "started_at"]

    def __str__(self):
        return f"{self.job} → {self.machine} ({self.runs} corridas)"


class Tablero(ProductionJob):
    """Proxy para tener en el admin el 'Tablero de producción' (panel general)."""

    class Meta:
        proxy = True
        verbose_name = _("Tablero de producción")
        verbose_name_plural = _("Tablero de producción")


class HerramientaCategoria(models.Model):
    """
    Categoría de herramienta/bien de uso (Maquinaria, Herramienta manual,
    Mobiliario, Electrónica, Otro...). Editable desde el admin, mismo patrón
    que `inventory.AggregateCategory`/`gastos.CategoriaGasto`: se agregan o
    editan categorías nuevas sin programar ni migrar nada. Borrar una
    categoría en uso está bloqueado (`on_delete=PROTECT` en `Herramienta`)
    para no perder la clasificación de lo ya cargado con ella.
    """

    nombre = models.CharField(_("Nombre"), max_length=50, unique=True)

    class Meta:
        verbose_name = _("Categoría de herramienta")
        verbose_name_plural = _("Categorías de herramienta")
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


def _default_herramienta_categoria():
    """
    Categoría que se preselecciona al cargar una Herramienta nueva: "Otro" si
    existe, si no la primera que haya. Devuelve None si todavía no hay
    ninguna categoría cargada.
    """
    return (
        HerramientaCategoria.objects.filter(nombre="Otro")
        .values_list("id", flat=True)
        .first()
        or HerramientaCategoria.objects.order_by("id").values_list("id", flat=True).first()
    )


class Herramienta(models.Model):
    """
    Un bien de uso / herramienta del taller que NO es una impresora (ver
    `Maquina` para eso): termoformadora, soldador, mobiliario, herramienta
    manual, etc. Vive en su propia sección "Herramientas", aislada de
    Métricas/Panel de costos por ahora (no entra en ningún cálculo de costos
    de producción ni en la depreciación de Máquinas).

    La depreciación es OPCIONAL por artículo (`se_deprecia`): una
    termoformadora puede depreciarse linealmente a lo largo de su vida útil,
    pero una herramienta manual sin valor de reventa (ej. un sacabocados) no
    necesita cargarle nada de eso.

    Autogenera un borrador de `MovimientoCaja` (EGRESO) al comprarla, mismo
    mecanismo que `Presupuesto`/`Gasto`: solo si el medio de pago es de
    "caja inmediata" (Efectivo/Transferencia/Mercado Pago/Ualá) y hay una
    única `CuentaCaja` configurada para ese medio. Tarjeta y cuotas siempre
    se cargan a mano (la plata no sale ese mismo día).
    """

    class Estado(models.TextChoices):
        ACTIVA = "ACTIVA", _("Activa")
        REPARACION = "REPARACION", _("En reparación")
        BAJA = "BAJA", _("De baja")
        VENDIDA = "VENDIDA", _("Vendida")

    class MedioPago(models.TextChoices):
        EFECTIVO = "EFECTIVO", _("Efectivo")
        TRANSFERENCIA = "TRANSFERENCIA", _("Transferencia")
        MERCADOPAGO = "MERCADOPAGO", _("Mercado Pago")
        UALA = "UALA", _("Ualá")
        TARJETA = "TARJETA", _("Tarjeta")
        OTRO = "OTRO", _("Otro")

    # Mismos medios "de caja inmediata" que `Presupuesto`/`Gasto`: la plata
    # sale el mismo día, así que son los únicos que disparan la
    # autogeneración de un borrador de `MovimientoCaja`.
    CAJA_INMEDIATA_MEDIOS_PAGO = {
        MedioPago.EFECTIVO,
        MedioPago.TRANSFERENCIA,
        MedioPago.MERCADOPAGO,
        MedioPago.UALA,
    }

    # --- Identificación ---
    nombre = models.CharField(
        _("Nombre"),
        max_length=150,
        help_text=_("Ej: Termoformadora de mesa, Sacabocados manual."),
    )
    categoria = models.ForeignKey(
        HerramientaCategoria,
        verbose_name=_("Categoría"),
        on_delete=models.PROTECT,
        related_name="herramientas",
        default=_default_herramienta_categoria,
    )
    numero_serie = models.CharField(_("Número de serie"), max_length=100, blank=True)
    ubicacion = models.CharField(_("Ubicación"), max_length=150, blank=True)

    # --- Adquisición ---
    fecha_compra = models.DateField(_("Fecha de compra"), default=timezone.localdate)
    proveedor = models.CharField(_("Proveedor"), max_length=150, blank=True)
    costo_adquisicion = models.DecimalField(
        _("Costo de adquisición ($)"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
    )
    comprobante = models.CharField(
        _("Comprobante / factura"),
        max_length=150,
        blank=True,
        help_text=_(
            "Referencia del comprobante (ej: \"Factura A 0001-00001234\"). No "
            "se adjunta archivo: lo subido a /media no persiste en producción "
            "(Vercel es serverless)."
        ),
    )
    medio_pago = models.CharField(
        _("Medio de pago"),
        max_length=20,
        choices=MedioPago.choices,
        blank=True,
        default="",
        help_text=_(
            "Cómo se pagó. Efectivo/Transferencia/Mercado Pago/Ualá generan "
            "un borrador en el Panel de caja para revisar y confirmar; "
            "Tarjeta y cuotas se cargan a mano."
        ),
    )
    cuota_actual = models.PositiveSmallIntegerField(
        _("Cuota actual"),
        null=True,
        blank=True,
        help_text=_(
            "Si se compró en cuotas, qué número de cuota es este pago puntual "
            "(se carga igual que un Gasto: una cuota por pago real)."
        ),
    )
    cuotas_totales = models.PositiveSmallIntegerField(
        _("Cuotas totales"), null=True, blank=True
    )

    # --- Depreciación (opcional) ---
    se_deprecia = models.BooleanField(
        _("Se deprecia"),
        default=False,
        help_text=_(
            "Activalo solo si este bien tiene valor de reventa y tiene "
            "sentido depreciarlo (ej: una termoformadora). Dejalo apagado "
            "para herramientas sin valor de reventa (ej: un sacabocados)."
        ),
    )
    vida_util_meses = models.PositiveIntegerField(
        _("Vida útil (meses)"),
        null=True,
        blank=True,
        help_text=_("Requerido si \"Se deprecia\" está activo."),
    )
    valor_residual = models.DecimalField(
        _("Valor residual ($)"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_("Valor estimado al final de la vida útil (puede ser 0)."),
    )

    # --- Estado / baja ---
    estado = models.CharField(
        _("Estado"), max_length=15, choices=Estado.choices, default=Estado.ACTIVA
    )
    fecha_baja = models.DateField(_("Fecha de baja"), null=True, blank=True)
    motivo_baja = models.CharField(_("Motivo de baja"), max_length=200, blank=True)
    precio_venta = models.DecimalField(
        _("Precio de venta ($)"),
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Si se vendió, cuánto se cobró por ella."),
    )

    notas = models.TextField(_("Notas"), blank=True)
    created_at = models.DateTimeField(_("Creada"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizada"), auto_now=True)

    class Meta:
        verbose_name = _("Herramienta / bien de uso")
        verbose_name_plural = _("Herramientas")
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.se_deprecia and not self.vida_util_meses:
            raise ValidationError(
                {
                    "vida_util_meses": _(
                        "Completá la vida útil (en meses) para depreciar este bien."
                    )
                }
            )
        if bool(self.cuotas_totales) != bool(self.cuota_actual):
            raise ValidationError(
                _(
                    "Completá tanto \"Cuota actual\" como \"Cuotas totales\", o "
                    "dejá los dos vacíos."
                )
            )
        if (
            self.cuotas_totales
            and self.cuota_actual
            and self.cuota_actual > self.cuotas_totales
        ):
            raise ValidationError(
                _("La \"Cuota actual\" no puede ser mayor que las \"Cuotas totales\".")
            )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._autogenerar_movimiento_caja()

    def _autogenerar_movimiento_caja(self):
        """
        Igual mecanismo que `Presupuesto`/`Gasto`: si el medio de pago es de
        "caja inmediata" y hay una única `CuentaCaja` configurada para ese
        medio, crea un borrador de `MovimientoCaja` (EGRESO) para revisar y
        confirmar desde el Panel de caja. No se genera si no hay ninguna
        cuenta configurada (o hay más de una, ambiguo): en ese caso se carga
        a mano. Import diferido para evitar un ciclo (`caja` depende de
        `production`, no al revés). Idempotente: no duplica el borrador
        automático de una misma herramienta.
        """
        if self.medio_pago not in self.CAJA_INMEDIATA_MEDIOS_PAGO:
            return

        from caja.models import CuentaCaja, MovimientoCaja

        if MovimientoCaja.objects.filter(
            herramienta=self, origen_automatico=True
        ).exists():
            return

        cuentas = CuentaCaja.objects.filter(
            medio_pago_default=self.medio_pago, is_active=True
        )
        if cuentas.count() != 1:
            return

        from django.utils.translation import gettext

        MovimientoCaja.crear_borrador_automatico(
            cuenta=cuentas.first(),
            tipo=MovimientoCaja.Tipo.EGRESO,
            monto=self.costo_adquisicion,
            fecha=self.fecha_compra,
            concepto=gettext("Compra herramienta #%(pk)s — %(nombre)s")
            % {"pk": self.pk, "nombre": self.nombre},
            herramienta=self,
        )

    # ---- Depreciación ----

    @property
    def meses_transcurridos(self) -> int:
        """
        Meses enteros transcurridos desde la compra hasta hoy (o hasta la
        fecha de baja, si ya se dio de baja). Nunca negativo.
        """
        fin = self.fecha_baja or timezone.localdate()
        if fin < self.fecha_compra:
            return 0
        meses = (fin.year - self.fecha_compra.year) * 12 + (
            fin.month - self.fecha_compra.month
        )
        if fin.day < self.fecha_compra.day:
            meses -= 1
        return max(meses, 0)

    @property
    def depreciacion_mensual(self) -> Decimal:
        """Depreciación lineal mensual: (costo − valor residual) / vida útil."""
        if not self.se_deprecia or not self.vida_util_meses:
            return Decimal("0")
        base = Decimal(self.costo_adquisicion or 0) - Decimal(self.valor_residual or 0)
        if base <= 0:
            return Decimal("0")
        return (base / Decimal(self.vida_util_meses)).quantize(Decimal("0.01"))

    @property
    def depreciacion_acumulada(self) -> Decimal:
        """Depreciación mensual × meses transcurridos, topeada al valor depreciable."""
        if not self.se_deprecia:
            return Decimal("0")
        base = Decimal(self.costo_adquisicion or 0) - Decimal(self.valor_residual or 0)
        if base <= 0:
            return Decimal("0")
        acumulada = self.depreciacion_mensual * Decimal(self.meses_transcurridos)
        return min(acumulada, base).quantize(Decimal("0.01"))

    @property
    def valor_contable_neto(self) -> Decimal:
        """Costo de adquisición menos depreciación acumulada."""
        neto = Decimal(self.costo_adquisicion or 0) - self.depreciacion_acumulada
        return neto.quantize(Decimal("0.01"))

    @property
    def esta_totalmente_depreciada(self) -> bool:
        if not self.se_deprecia or not self.vida_util_meses:
            return False
        return self.meses_transcurridos >= self.vida_util_meses


class MantenimientoHerramienta(models.Model):
    """
    Registro histórico de un mantenimiento/reparación de una Herramienta
    (inline de solo carga, sin automatismos). No genera movimiento de caja
    automático: si implicó un pago real, se carga como Gasto aparte.
    """

    herramienta = models.ForeignKey(
        Herramienta,
        verbose_name=_("Herramienta"),
        on_delete=models.CASCADE,
        related_name="mantenimientos",
    )
    fecha = models.DateField(_("Fecha"), default=timezone.localdate)
    descripcion = models.CharField(_("Descripción"), max_length=255)
    costo = models.DecimalField(
        _("Costo ($)"), max_digits=10, decimal_places=2, default=Decimal("0"), blank=True
    )
    proveedor = models.CharField(_("Proveedor / técnico"), max_length=150, blank=True)

    class Meta:
        verbose_name = _("Mantenimiento")
        verbose_name_plural = _("Mantenimientos")
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.fecha} — {self.descripcion}"
