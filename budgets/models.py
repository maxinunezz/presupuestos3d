from decimal import ROUND_HALF_UP, Decimal

from django.db import models, transaction
from django.utils import timezone

from inventory.models import Aggregate, Filament, StockMovement


class BudgetNotApprovableError(Exception):
    """
    Se intentó aprobar un presupuesto que no está en un estado aprobable
    (por ejemplo, uno ya aprobado, en producción, completado o cancelado).
    Evita descontar stock dos veces sobre el mismo presupuesto.
    """


class Budget(models.Model):
    """
    Un presupuesto / pieza a cotizar. Agrupa líneas de filamento (una por
    cada color/material que entra en la pieza) y líneas de agregados
    (insumos extra: argollas, packaging, llaveros, etc.).
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Borrador"
        SENT = "SENT", "Enviado"
        APPROVED = "APPROVED", "Aprobado"
        IN_PRODUCTION = "IN_PRODUCTION", "En producción"
        COMPLETED = "COMPLETED", "Completado"
        CANCELLED = "CANCELLED", "Cancelado"

    client_name = models.CharField("Cliente", max_length=150, blank=True)
    name = models.CharField("Nombre / pieza", max_length=200)
    description = models.TextField("Descripción", blank=True)

    quantity = models.PositiveIntegerField(
        "Cantidad de piezas",
        default=1,
        help_text=(
            "Cuántas piezas iguales se cotizan. Las líneas de filamento y "
            "agregados describen UNA pieza; el costo se multiplica por esta cantidad."
        ),
    )

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

    fixed_cost = models.DecimalField(
        "Costo fijo por pedido",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Costo que se cobra una sola vez por pedido (setup, diseño, envío, etc.).",
    )

    margin_percent = models.DecimalField(
        "Margen (%)", max_digits=5, decimal_places=2, default=0
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
    approved_at = models.DateTimeField("Aprobado el", null=True, blank=True)

    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Presupuesto"
        verbose_name_plural = "Presupuestos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} {self.name} ({self.get_status_display()})"

    # ---- Costos calculados ----
    #
    # Las líneas describen UNA pieza. Los costos "_unit" son por pieza; los
    # totales de pedido multiplican por `quantity` y suman el costo fijo.

    @property
    def waste_multiplier(self) -> Decimal:
        return Decimal("1") + (self.waste_percent / Decimal("100"))

    @property
    def material_cost(self) -> Decimal:
        """Costo de filamento de UNA pieza, sin merma."""
        return sum(
            (line.line_cost for line in self.filament_lines.all()),
            Decimal("0"),
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
            (line.line_cost for line in self.aggregate_lines.all()),
            Decimal("0"),
        )

    @property
    def machine_cost(self) -> Decimal:
        """Costo de máquina de UNA pieza."""
        return (self.print_time_hours * self.machine_cost_per_hour).quantize(
            Decimal("0.01")
        )

    @property
    def labor_cost(self) -> Decimal:
        """Costo de mano de obra / post-proceso de UNA pieza."""
        return (self.post_processing_hours * self.labor_cost_per_hour).quantize(
            Decimal("0.01")
        )

    @property
    def unit_cost(self) -> Decimal:
        """Costo total de producir UNA pieza (material + merma + agregados + máquina + mano de obra)."""
        return (
            self.material_cost
            + self.material_waste_cost
            + self.aggregate_cost
            + self.machine_cost
            + self.labor_cost
        ).quantize(Decimal("0.01"))

    @property
    def production_cost(self) -> Decimal:
        """Costo de producir todas las piezas (unit_cost × cantidad), sin costo fijo."""
        return (self.unit_cost * self.quantity).quantize(Decimal("0.01"))

    @property
    def subtotal(self) -> Decimal:
        """Costo total del pedido antes del margen: producción + costo fijo."""
        return (self.production_cost + self.fixed_cost).quantize(Decimal("0.01"))

    @property
    def total(self) -> Decimal:
        """Total final del pedido, con margen y redondeo."""
        margin_multiplier = Decimal("1") + (self.margin_percent / Decimal("100"))
        total = (self.subtotal * margin_multiplier).quantize(Decimal("0.01"))

        if self.round_to and self.round_to > 0:
            steps = (total / self.round_to).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            total = (steps * self.round_to).quantize(Decimal("0.01"))

        return total

    @property
    def unit_price(self) -> Decimal:
        """Precio de venta por pieza (total / cantidad)."""
        if not self.quantity:
            return Decimal("0.00")
        return (self.total / self.quantity).quantize(Decimal("0.01"))

    # ---- Stock ----

    def filament_grams_needed(self, line) -> Decimal:
        """Gramos reales que consume una línea: por pieza × cantidad × (1 + merma)."""
        return (line.grams_used * self.quantity * self.waste_multiplier).quantize(
            Decimal("0.01")
        )

    def aggregate_qty_needed(self, line) -> Decimal:
        """Unidades reales que consume una línea de agregado: por pieza × cantidad."""
        return (line.quantity * self.quantity).quantize(Decimal("0.01"))

    def check_stock_availability(self):
        """
        Devuelve una lista de dicts describiendo qué líneas no tienen stock
        suficiente, sin modificar nada. Útil para mostrar un warning antes
        de aprobar. Tiene en cuenta la cantidad de piezas y la merma.
        """
        shortages = []

        for line in self.filament_lines.select_related("filament").all():
            needed = self.filament_grams_needed(line)
            if not line.filament.has_enough_stock(needed):
                missing = needed - line.filament.stock_grams
                shortages.append(
                    {
                        "type": "filament",
                        "item": str(line.filament),
                        "needed": needed,
                        "available": line.filament.stock_grams,
                        "missing": max(missing, Decimal("0")),
                    }
                )

        for line in self.aggregate_lines.select_related("aggregate").all():
            needed = self.aggregate_qty_needed(line)
            if not line.aggregate.has_enough_stock(needed):
                missing = needed - line.aggregate.stock_quantity
                shortages.append(
                    {
                        "type": "aggregate",
                        "item": str(line.aggregate),
                        "needed": needed,
                        "available": line.aggregate.stock_quantity,
                        "missing": max(missing, Decimal("0")),
                    }
                )

        return shortages

    def approve(self):
        """
        Pasa el presupuesto a APPROVED, descuenta stock (cappeado en 0,
        nunca negativo) y registra un StockMovement por cada línea.
        Devuelve la lista de faltantes detectados (igual formato que
        check_stock_availability), para que la vista pueda mostrarlos
        como warning sin bloquear la operación.

        Solo se puede aprobar un presupuesto en estado Borrador o Enviado. Si
        ya está aprobado (o más avanzado/cancelado) lanza BudgetNotApprovableError
        para no volver a descontar stock sobre el mismo presupuesto.
        """
        approvable_statuses = {Budget.Status.DRAFT, Budget.Status.SENT}
        if self.status not in approvable_statuses:
            raise BudgetNotApprovableError(
                f"No se puede aprobar el presupuesto #{self.pk}: su estado es "
                f"'{self.get_status_display()}'. Solo se pueden aprobar "
                f"presupuestos en estado Borrador o Enviado."
            )

        shortages = self.check_stock_availability()

        # Todo el descuento de stock, el registro de movimientos y el cambio de
        # estado ocurren dentro de una transacción: si algo falla a mitad de
        # camino, se revierte todo y no queda stock descontado a medias.
        with transaction.atomic():
            for line in self.filament_lines.select_related("filament").all():
                needed = self.filament_grams_needed(line)
                shortage = line.filament.deduct_stock(needed)
                note = ""
                if shortage > 0:
                    note = f"Faltaron {shortage}g (stock insuficiente al aprobar)"
                StockMovement.objects.create(
                    filament=line.filament,
                    quantity=-needed,
                    reason=StockMovement.Reason.BUDGET_APPROVED,
                    related_budget=self,
                    note=note,
                )

            for line in self.aggregate_lines.select_related("aggregate").all():
                needed = self.aggregate_qty_needed(line)
                shortage = line.aggregate.deduct_stock(needed)
                note = ""
                if shortage > 0:
                    note = f"Faltaron {shortage} (stock insuficiente al aprobar)"
                StockMovement.objects.create(
                    aggregate=line.aggregate,
                    quantity=-needed,
                    reason=StockMovement.Reason.BUDGET_APPROVED,
                    related_budget=self,
                    note=note,
                )

            self.status = Budget.Status.APPROVED
            self.approved_at = timezone.now()
            self.save(update_fields=["status", "approved_at", "updated_at"])

        return shortages

    def duplicate(self):
        """
        Crea una copia del presupuesto con todas sus líneas, en estado Borrador.
        Las líneas se vuelven a costear con el precio ACTUAL del inventario
        (re-cotiza a hoy), ideal para un pedido repetido. Devuelve la copia.
        """
        with transaction.atomic():
            copy = Budget.objects.create(
                client_name=self.client_name,
                name=f"{self.name} (copia)",
                description=self.description,
                quantity=self.quantity,
                print_time_hours=self.print_time_hours,
                machine_cost_per_hour=self.machine_cost_per_hour,
                waste_percent=self.waste_percent,
                post_processing_hours=self.post_processing_hours,
                labor_cost_per_hour=self.labor_cost_per_hour,
                fixed_cost=self.fixed_cost,
                margin_percent=self.margin_percent,
                round_to=self.round_to,
                status=Budget.Status.DRAFT,
            )

            for line in self.filament_lines.all():
                # unit_cost=None => save() lo congela con el precio actual del filamento.
                BudgetFilamentLine.objects.create(
                    budget=copy, filament=line.filament, grams_used=line.grams_used
                )

            for line in self.aggregate_lines.all():
                BudgetAggregateLine.objects.create(
                    budget=copy, aggregate=line.aggregate, quantity=line.quantity
                )

        return copy


class BudgetFilamentLine(models.Model):
    """
    Una línea de filamento usado en un presupuesto. Una pieza multicolor
    tiene una línea por cada filamento distinto que entra en ella, lo que
    permite calcular el costo real en vez de usar el precio del más caro.
    """

    budget = models.ForeignKey(
        Budget, on_delete=models.CASCADE, related_name="filament_lines"
    )
    filament = models.ForeignKey(
        Filament, on_delete=models.PROTECT, related_name="budget_lines"
    )
    grams_used = models.DecimalField("Gramos usados", max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        "Costo por gramo (congelado)",
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            "Costo por gramo guardado al momento de cotizar. Si se deja vacío, "
            "se toma automáticamente el precio actual del filamento. Queda fijo "
            "aunque después cambie el precio del filamento."
        ),
    )

    class Meta:
        verbose_name = "Línea de filamento"
        verbose_name_plural = "Líneas de filamento"

    def __str__(self):
        return f"{self.filament} - {self.grams_used}g"

    def save(self, *args, **kwargs):
        # Congela el precio del gramo al crear la línea si no vino uno explícito.
        if self.unit_cost is None and self.filament_id:
            self.unit_cost = self.filament.cost_per_gram
        super().save(*args, **kwargs)

    @property
    def effective_unit_cost(self) -> Decimal:
        """Costo por gramo a usar: el congelado si existe, si no el precio actual."""
        if self.unit_cost is not None:
            return self.unit_cost
        return self.filament.cost_per_gram

    @property
    def line_cost(self) -> Decimal:
        return (self.grams_used * self.effective_unit_cost).quantize(Decimal("0.01"))


class BudgetAggregateLine(models.Model):
    """
    Una línea de agregado (insumo no-filamento) usado en un presupuesto.
    """

    budget = models.ForeignKey(
        Budget, on_delete=models.CASCADE, related_name="aggregate_lines"
    )
    aggregate = models.ForeignKey(
        Aggregate, on_delete=models.PROTECT, related_name="budget_lines"
    )
    quantity = models.DecimalField("Cantidad", max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        "Costo por unidad (congelado)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Costo por unidad guardado al momento de cotizar. Si se deja vacío, "
            "se toma automáticamente el precio actual del agregado. Queda fijo "
            "aunque después cambie el precio del agregado."
        ),
    )

    class Meta:
        verbose_name = "Línea de agregado"
        verbose_name_plural = "Líneas de agregado"

    def __str__(self):
        return f"{self.aggregate} x{self.quantity}"

    def save(self, *args, **kwargs):
        # Congela el precio unitario al crear la línea si no vino uno explícito.
        if self.unit_cost is None and self.aggregate_id:
            self.unit_cost = self.aggregate.cost_per_unit
        super().save(*args, **kwargs)

    @property
    def effective_unit_cost(self) -> Decimal:
        """Costo por unidad a usar: el congelado si existe, si no el precio actual."""
        if self.unit_cost is not None:
            return self.unit_cost
        return self.aggregate.cost_per_unit

    @property
    def line_cost(self) -> Decimal:
        return (self.quantity * self.effective_unit_cost).quantize(Decimal("0.01"))
