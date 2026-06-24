from decimal import ROUND_HALF_UP, Decimal

from django.db import models, transaction
from django.utils import timezone

from inventory.models import Aggregate, Filament


def _dec(value) -> Decimal:
    """
    Normaliza un valor a Decimal. Los DecimalField con default=0 son int en
    memoria hasta que el objeto pasa por la DB; sin esto, multiplicar dos
    campos recién creados da un int y `.quantize()` revienta.
    """
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


# ===========================================================================
#  COSTEO DE PRODUCTOS  +  PRESUPUESTOS
#
#  La idea:
#   - Producto: el costeo de UNA pieza/producto. Define materiales, máquina,
#     mano de obra y margen, y calcula su costo y precio de venta por unidad.
#     Se carga una sola vez y se reutiliza.
#   - Presupuesto: una cotización para un cliente. Agrupa varios productos
#     ya costeados, cada uno con su cantidad, y suma el total. Al aprobarlo,
#     descuenta del inventario la materia prima de todos sus productos.
# ===========================================================================


class Producto(models.Model):
    """
    Costeo de un producto/pieza. Describe el consumo de UNA unidad
    (líneas de filamento y agregados) más los costos de impresión, máquina
    y mano de obra, y calcula su costo y precio de venta unitario.
    """

    name = models.CharField("Nombre / pieza", max_length=200)
    description = models.TextField("Descripción", blank=True)

    is_multicolor = models.BooleanField(
        "Multicolor (AMS)",
        default=False,
        help_text=(
            "Marcá si la pieza usa varios colores/filamentos en simultáneo. "
            "Solo se puede imprimir en máquinas con AMS (ej. Bambu Lab), no en la Ender."
        ),
    )

    # --- Impresión y máquina ---
    print_time_hours = models.DecimalField(
        "Tiempo de impresión por pieza (hs)", max_digits=6, decimal_places=2, default=0
    )
    machine_cost_per_hour = models.DecimalField(
        "Costo de máquina por hora", max_digits=10, decimal_places=2, default=0
    )
    waste_percent = models.DecimalField(
        "Merma de material (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=(
            "Desperdicio de filamento por purga (multicolor), soportes y fallas. "
            "Se aplica sobre el costo y el consumo de material."
        ),
    )

    # --- Mano de obra / post-proceso ---
    post_processing_hours = models.DecimalField(
        "Post-proceso por pieza (hs)",
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Tiempo de armado, lijado, pintado, pegado de agregados, etc.",
    )
    labor_cost_per_hour = models.DecimalField(
        "Costo de mano de obra por hora", max_digits=10, decimal_places=2, default=0
    )

    # --- Precio ---
    margin_percent = models.DecimalField(
        "Margen (%)", max_digits=5, decimal_places=2, default=0
    )
    round_to = models.DecimalField(
        "Redondear precio a múltiplo de",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Ej: 100 redondea el precio a la centena más cercana. 0 = sin redondeo.",
    )

    # --- Archivo del modelo (solo local por ahora) ---
    gcode = models.TextField(
        "G-code",
        blank=True,
        help_text="Pegá acá el g-code del laminador (opcional).",
    )
    model_file = models.FileField(
        "Archivo .3mf / modelo",
        upload_to="productos/",
        blank=True,
        help_text="Subí el .3mf o archivo del modelo (opcional, solo desarrollo local).",
    )

    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Costeo de producto"
        verbose_name_plural = "Costeo de productos"
        ordering = ["name"]

    def __str__(self):
        return self.name

    # ---- Costos calculados (todos por UNA unidad) ----

    @property
    def waste_multiplier(self) -> Decimal:
        return Decimal("1") + (self.waste_percent / Decimal("100"))

    @property
    def material_cost(self) -> Decimal:
        """Costo de filamento de UNA pieza, sin merma."""
        return sum(
            (line.line_cost for line in self.filament_lines.all()), Decimal("0")
        )

    @property
    def material_waste_cost(self) -> Decimal:
        """Costo del desperdicio de material de UNA pieza."""
        return (self.material_cost * (self.waste_percent / Decimal("100"))).quantize(
            Decimal("0.01")
        )

    @property
    def aggregate_cost(self) -> Decimal:
        """Costo de agregados de UNA pieza."""
        return sum(
            (line.line_cost for line in self.aggregate_lines.all()), Decimal("0")
        )

    @property
    def machine_cost(self) -> Decimal:
        return (_dec(self.print_time_hours) * _dec(self.machine_cost_per_hour)).quantize(
            Decimal("0.01")
        )

    @property
    def labor_cost(self) -> Decimal:
        return (_dec(self.post_processing_hours) * _dec(self.labor_cost_per_hour)).quantize(
            Decimal("0.01")
        )

    @property
    def unit_cost(self) -> Decimal:
        """Costo total de producir UNA pieza."""
        return (
            self.material_cost
            + self.material_waste_cost
            + self.aggregate_cost
            + self.machine_cost
            + self.labor_cost
        ).quantize(Decimal("0.01"))

    @property
    def unit_price(self) -> Decimal:
        """Precio de venta por pieza: costo + margen, redondeado."""
        margin_multiplier = Decimal("1") + (self.margin_percent / Decimal("100"))
        price = (self.unit_cost * margin_multiplier).quantize(Decimal("0.01"))

        if self.round_to and self.round_to > 0:
            steps = (price / self.round_to).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            price = (steps * self.round_to).quantize(Decimal("0.01"))

        return price

    # ---- Stock (consumo para una cantidad dada de piezas) ----

    def filament_grams_needed(self, line, quantity) -> Decimal:
        """Gramos reales que consume una línea: por pieza × cantidad × (1 + merma)."""
        return (line.grams_used * quantity * self.waste_multiplier).quantize(
            Decimal("0.01")
        )

    def aggregate_qty_needed(self, line, quantity) -> Decimal:
        """Unidades reales que consume una línea de agregado: por pieza × cantidad."""
        return (line.quantity * quantity).quantize(Decimal("0.01"))


class ProductoFilamentLine(models.Model):
    """Una línea de filamento que entra en UNA unidad de un producto."""

    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, related_name="filament_lines"
    )
    filament = models.ForeignKey(
        Filament, on_delete=models.PROTECT, related_name="producto_lines"
    )
    grams_used = models.DecimalField("Gramos usados", max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        "Costo por gramo (congelado)",
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            "Costo por gramo guardado al momento de costear. Si se deja vacío, "
            "se toma el precio actual del filamento."
        ),
    )

    class Meta:
        verbose_name = "Línea de filamento"
        verbose_name_plural = "Líneas de filamento"

    def __str__(self):
        return f"{self.filament} - {self.grams_used}g"

    def save(self, *args, **kwargs):
        if self.unit_cost is None and self.filament_id:
            self.unit_cost = self.filament.cost_per_gram
        super().save(*args, **kwargs)

    @property
    def effective_unit_cost(self) -> Decimal:
        if self.unit_cost is not None:
            return self.unit_cost
        return self.filament.cost_per_gram

    @property
    def line_cost(self) -> Decimal:
        return (self.grams_used * self.effective_unit_cost).quantize(Decimal("0.01"))


class ProductoAggregateLine(models.Model):
    """Una línea de agregado (insumo no-filamento) que entra en UNA unidad."""

    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, related_name="aggregate_lines"
    )
    aggregate = models.ForeignKey(
        Aggregate, on_delete=models.PROTECT, related_name="producto_lines"
    )
    quantity = models.DecimalField("Cantidad", max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        "Costo por unidad (congelado)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Costo por unidad guardado al costear. Si se deja vacío, se toma "
            "el precio actual del agregado."
        ),
    )

    class Meta:
        verbose_name = "Línea de agregado"
        verbose_name_plural = "Líneas de agregado"

    def __str__(self):
        return f"{self.aggregate} x{self.quantity}"

    def save(self, *args, **kwargs):
        if self.unit_cost is None and self.aggregate_id:
            self.unit_cost = self.aggregate.cost_per_unit
        super().save(*args, **kwargs)

    @property
    def effective_unit_cost(self) -> Decimal:
        if self.unit_cost is not None:
            return self.unit_cost
        return self.aggregate.cost_per_unit

    @property
    def line_cost(self) -> Decimal:
        return (self.quantity * self.effective_unit_cost).quantize(Decimal("0.01"))


class PresupuestoNotApprovableError(Exception):
    """
    Se intentó aprobar un presupuesto que no está en estado aprobable.
    Evita descontar stock dos veces sobre el mismo presupuesto.
    """


class Presupuesto(models.Model):
    """
    Cotización para un cliente. Agrupa varios productos ya costeados, cada uno
    con su cantidad, y suma el total. Al aprobarlo, descuenta del inventario
    la materia prima de todos sus productos.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        SENT = "SENT", "Enviado"
        APPROVED = "APPROVED", "Aprobado"
        IN_PRODUCTION = "IN_PRODUCTION", "En producción"
        COMPLETED = "COMPLETED", "Completado"
        CANCELLED = "CANCELLED", "Cancelado"

    client_name = models.CharField("Cliente", max_length=150)
    description = models.TextField("Notas / descripción", blank=True)

    fixed_cost = models.DecimalField(
        "Costo fijo por pedido",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Costo que se cobra una sola vez por pedido (setup, envío, etc.).",
    )
    round_to = models.DecimalField(
        "Redondear total a múltiplo de",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Ej: 100 redondea el total a la centena más cercana. 0 = sin redondeo.",
    )

    status = models.CharField(
        "Estado", max_length=20, choices=Status.choices, default=Status.DRAFT
    )

    # --- Fechas por estado (reloj de producción) ---
    sent_at = models.DateTimeField("Enviado el", null=True, blank=True)
    approved_at = models.DateTimeField("Aprobado el", null=True, blank=True)
    production_started_at = models.DateTimeField(
        "Producción iniciada el", null=True, blank=True
    )
    production_finished_at = models.DateTimeField(
        "Producción terminada el", null=True, blank=True
    )
    completed_at = models.DateTimeField("Completado el", null=True, blank=True)

    # --- Entrega ---
    due_date = models.DateTimeField(
        "Fecha de entrega",
        null=True,
        blank=True,
        help_text=(
            "Se calcula sola con la cola de producción. Podés pisarla a mano: "
            "si la editás, queda fija y deja de recalcularse."
        ),
    )
    due_date_is_manual = models.BooleanField(
        "Entrega fijada a mano", default=False
    )

    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Presupuesto"
        verbose_name_plural = "Presupuestos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.client_name} ({self.get_status_display()})"

    # ---- Totales ----

    @property
    def items_total(self) -> Decimal:
        """Suma de todas las líneas (producto × cantidad), sin costo fijo."""
        return sum((item.line_total for item in self.items.all()), Decimal("0"))

    @property
    def subtotal(self) -> Decimal:
        return (self.items_total + self.fixed_cost).quantize(Decimal("0.01"))

    @property
    def total(self) -> Decimal:
        """Total final con redondeo (el margen ya está en cada producto)."""
        total = self.subtotal
        if self.round_to and self.round_to > 0:
            steps = (total / self.round_to).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            total = (steps * self.round_to).quantize(Decimal("0.01"))
        return total

    @property
    def total_pieces(self) -> int:
        return sum(item.quantity for item in self.items.all())

    # ---- Producción / tiempos ----

    @property
    def total_print_hours(self) -> Decimal:
        """Horas de impresión del pedido: Σ productos × cantidad × tiempo/pieza."""
        return sum(
            (
                Decimal(item.quantity) * _dec(item.producto.print_time_hours)
                for item in self.items.select_related("producto").all()
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

    @property
    def total_post_processing_hours(self) -> Decimal:
        """Suma del post-proceso de todos los productos del pedido × cantidad."""
        return sum(
            (
                Decimal(item.quantity) * _dec(item.producto.post_processing_hours)
                for item in self.items.select_related("producto").all()
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

    @property
    def estimated_print_ready(self):
        """
        Momento estimado en que termina de imprimirse TODO el pedido: el máximo
        fin de impresión entre sus trabajos abiertos. None si no hay trabajos.
        """
        ends = [
            job.estimated_print_end
            for job in self.jobs.all()
            if job.is_open and job.estimated_print_end
        ]
        return max(ends) if ends else None

    @property
    def estimated_delivery(self):
        """
        Entrega estimada = fin de impresión de todo el pedido + post-proceso total.
        El post-proceso va en serie después de imprimir (lo hacés vos a mano).
        """
        from datetime import timedelta

        print_ready = self.estimated_print_ready
        if print_ready is None:
            return None
        return print_ready + timedelta(hours=float(self.total_post_processing_hours))

    def generate_jobs(self):
        """
        Crea un trabajo de producción por cada producto del presupuesto (si no
        existen ya), recomendando la máquina que se libera antes. No pisa
        asignaciones ni órdenes ya hechas a mano.
        """
        from production.models import ProductionJob
        from production.scheduler import machine_free_times, recommend_machine

        if self.jobs.exists():
            return  # ya tiene trabajos: no duplicar

        free = machine_free_times()
        # Asigna primero los trabajos más largos para balancear mejor las colas.
        items = sorted(
            self.items.select_related("producto").all(),
            key=lambda it: Decimal(it.quantity) * _dec(it.producto.print_time_hours),
            reverse=True,
        )
        for item in items:
            print_hours = Decimal(item.quantity) * _dec(item.producto.print_time_hours)
            machine, free = recommend_machine(
                print_hours,
                _free_cache=free,
                requires_multicolor=item.producto.is_multicolor,
            )
            order = 0
            if machine is not None:
                order = (
                    ProductionJob.objects.filter(machine=machine)
                    .exclude(
                        status__in=[
                            ProductionJob.Status.DONE,
                            ProductionJob.Status.CANCELLED,
                        ]
                    )
                    .count()
                )
            ProductionJob.objects.create(
                presupuesto=self,
                producto=item.producto,
                quantity=item.quantity,
                machine=machine,
                order=order,
            )

    def refresh_delivery(self):
        """
        Recalcula la cola completa y, si la entrega no fue fijada a mano,
        actualiza la fecha de entrega estimada del presupuesto.
        """
        from production.scheduler import persist_schedule

        persist_schedule()
        if not self.due_date_is_manual:
            # persist_schedule() actualizó los jobs en la DB; si la relación
            # estaba prefetcheada, limpiamos su caché para releer los tiempos
            # frescos al calcular la entrega.
            if hasattr(self, "_prefetched_objects_cache"):
                self._prefetched_objects_cache.pop("jobs", None)
            delivery = self.estimated_delivery
            if delivery is not None and delivery != self.due_date:
                self.due_date = delivery
                self.save(update_fields=["due_date", "updated_at"])

    # ---- Stock ----

    def check_stock_availability(self):
        """
        Suma el consumo de materia prima de todos los productos del presupuesto
        (cada uno × su cantidad) y devuelve los faltantes, sin modificar nada.
        """
        from collections import defaultdict

        fil_needed = defaultdict(Decimal)
        fil_obj = {}
        agg_needed = defaultdict(Decimal)
        agg_obj = {}

        for item in self.items.select_related("producto").all():
            producto = item.producto
            for line in producto.filament_lines.select_related("filament").all():
                needed = producto.filament_grams_needed(line, item.quantity)
                fil_needed[line.filament_id] += needed
                fil_obj[line.filament_id] = line.filament
            for line in producto.aggregate_lines.select_related("aggregate").all():
                needed = producto.aggregate_qty_needed(line, item.quantity)
                agg_needed[line.aggregate_id] += needed
                agg_obj[line.aggregate_id] = line.aggregate

        shortages = []
        for fid, needed in fil_needed.items():
            fil = fil_obj[fid]
            if not fil.has_enough_stock(needed):
                shortages.append(
                    {
                        "type": "filament",
                        "item": str(fil),
                        "needed": needed,
                        "available": fil.stock_grams,
                        "missing": max(needed - fil.stock_grams, Decimal("0")),
                    }
                )
        for aid, needed in agg_needed.items():
            agg = agg_obj[aid]
            if not agg.has_enough_stock(needed):
                shortages.append(
                    {
                        "type": "aggregate",
                        "item": str(agg),
                        "needed": needed,
                        "available": agg.stock_quantity,
                        "missing": max(needed - agg.stock_quantity, Decimal("0")),
                    }
                )
        return shortages

    def approve(self):
        """
        Pasa el presupuesto a APPROVED y lo mete a producción: genera los
        trabajos, los asigna a las máquinas y calcula la entrega estimada.

        NO descuenta stock acá: el material se descuenta cuando cada trabajo se
        marca como impreso (al imprimir es cuando realmente se gasta filamento).
        Devuelve los faltantes detectados (solo a modo de aviso).
        Solo se puede aprobar en estado Borrador o Enviado.
        """
        approvable_statuses = {Presupuesto.Status.DRAFT, Presupuesto.Status.SENT}
        if self.status not in approvable_statuses:
            raise PresupuestoNotApprovableError(
                f"No se puede aprobar el presupuesto #{self.pk}: su estado es "
                f"'{self.get_status_display()}'. Solo se pueden aprobar "
                f"presupuestos en estado Borrador o Enviado."
            )

        shortages = self.check_stock_availability()

        with transaction.atomic():
            self.status = Presupuesto.Status.APPROVED
            self.approved_at = timezone.now()
            self.save(update_fields=["status", "approved_at", "updated_at"])

            # Al aprobar, el pedido entra a producción: generamos sus trabajos,
            # los asignamos a las máquinas y calculamos la entrega estimada.
            self.generate_jobs()

        # Fuera de la transacción: recalcula cola y entrega.
        self.refresh_delivery()

        return shortages

    def apply_status_change(self, old_status):
        """
        Sincroniza las fechas de estado y dispara los efectos de producción
        cuando el estado se cambia "a mano" desde el admin (el dropdown del
        formulario, que no pasa por approve()).

        - Setea la fecha del nuevo estado si todavía está vacía (idempotente).
        - Al pasar a APPROVADO, genera la cola de producción (si no existe ya)
          y recalcula la entrega estimada.
        Devuelve la lista de faltantes si hubo aprobación, si no None.

        Pensado para llamarse DESPUÉS de guardar los ítems (en save_related),
        así generate_jobs() ve los productos del presupuesto.
        """
        Status = Presupuesto.Status
        if old_status == self.status:
            return None

        now = timezone.now()
        shortages = None

        if self.status == Status.SENT and not self.sent_at:
            self.sent_at = now
        elif self.status == Status.APPROVED:
            if not self.approved_at:
                self.approved_at = now
            shortages = self.check_stock_availability()
            self.generate_jobs()
        elif self.status == Status.IN_PRODUCTION and not self.production_started_at:
            self.production_started_at = now
        elif self.status == Status.COMPLETED:
            if not self.production_finished_at:
                self.production_finished_at = now
            if not self.completed_at:
                self.completed_at = now

        self.save(
            update_fields=[
                "sent_at",
                "approved_at",
                "production_started_at",
                "production_finished_at",
                "completed_at",
                "updated_at",
            ]
        )

        if self.status == Status.APPROVED:
            self.refresh_delivery()

        return shortages

    def sync_status_from_jobs(self):
        """
        Hace que el estado del presupuesto refleje la realidad de su cola de
        producción. Una vez aprobado, los TRABAJOS son la fuente de verdad de
        la etapa de producción:

          - todos los trabajos En cola            -> Aprobado
          - alguno Imprimiendo / alguno Impreso   -> En producción
          - todos los trabajos Impresos           -> Completado

        Solo AVANZA la etapa (nunca retrocede ni des-aprueba) y solo actúa si
        el presupuesto ya está Aprobado o más adelante. Setea las fechas de
        estado que falten. Devuelve True si cambió el estado.

        Así el estado del presupuesto se mantiene solo a partir de la cola: el
        usuario trabaja la cola (acá o en 'Trabajos de producción') y el pedido
        avanza de etapa automáticamente.
        """
        from production.models import ProductionJob

        Status = Presupuesto.Status
        order = {Status.APPROVED: 1, Status.IN_PRODUCTION: 2, Status.COMPLETED: 3}
        if self.status not in order:
            return False

        jobs = [
            j
            for j in self.jobs.all()
            if j.status != ProductionJob.Status.CANCELLED
        ]
        if not jobs:
            return False

        all_done = all(j.status == ProductionJob.Status.DONE for j in jobs)
        any_started = any(
            j.status in (ProductionJob.Status.PRINTING, ProductionJob.Status.DONE)
            for j in jobs
        )
        if all_done:
            target = Status.COMPLETED
        elif any_started:
            target = Status.IN_PRODUCTION
        else:
            target = Status.APPROVED

        if order[target] <= order[self.status]:
            return False  # no retroceder ni cambios sin avance

        now = timezone.now()
        self.status = target
        if not self.production_started_at:
            self.production_started_at = now
        if target == Status.COMPLETED:
            if not self.production_finished_at:
                self.production_finished_at = now
            if not self.completed_at:
                self.completed_at = now
        self.save(
            update_fields=[
                "status",
                "production_started_at",
                "production_finished_at",
                "completed_at",
                "updated_at",
            ]
        )
        return True


class Metricas(Presupuesto):
    """
    Proxy de Presupuesto para tener en el admin una página propia de 'Métricas'
    (panel de KPIs de ventas, producción e inventario). No crea tabla nueva: la
    vista se arma en el admin con budgets.metrics.
    """

    class Meta:
        proxy = True
        verbose_name = "Métrica"
        verbose_name_plural = "Métricas"


class PresupuestoItem(models.Model):
    """
    Una línea de presupuesto: un producto ya costeado, con la cantidad de
    piezas para este cliente y el precio unitario congelado.
    """

    presupuesto = models.ForeignKey(
        Presupuesto, on_delete=models.CASCADE, related_name="items"
    )
    producto = models.ForeignKey(
        Producto,
        on_delete=models.PROTECT,
        related_name="presupuesto_items",
        limit_choices_to={"is_active": True},
    )
    quantity = models.PositiveIntegerField("Cantidad de piezas", default=1)
    unit_price = models.DecimalField(
        "Precio unitario (congelado)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Precio por pieza guardado al armar el presupuesto. Si se deja "
            "vacío, se toma el precio actual del producto. Queda fijo aunque "
            "después cambie el costeo del producto."
        ),
    )

    class Meta:
        verbose_name = "Producto del presupuesto"
        verbose_name_plural = "Productos del presupuesto"

    def __str__(self):
        return f"{self.producto} x{self.quantity}"

    def save(self, *args, **kwargs):
        if self.unit_price is None and self.producto_id:
            self.unit_price = self.producto.unit_price
        super().save(*args, **kwargs)

    @property
    def effective_unit_price(self) -> Decimal:
        if self.unit_price is not None:
            return self.unit_price
        return self.producto.unit_price

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.effective_unit_price).quantize(Decimal("0.01"))
