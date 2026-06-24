from decimal import Decimal

from django.db import models


class Maquina(models.Model):
    """
    Una impresora 3D. Define el paralelismo de producción: cada máquina activa
    procesa su propia cola de trabajos.
    """

    name = models.CharField("Nombre", max_length=120, unique=True)
    is_active = models.BooleanField(
        "Activa",
        default=True,
        help_text="Si está inactiva, no se le asignan trabajos nuevos ni cuenta para la cola.",
    )
    supports_multicolor = models.BooleanField(
        "Imprime multicolor (AMS)",
        default=False,
        help_text=(
            "Marcá si la máquina puede imprimir piezas de varios colores en "
            "simultáneo (ej. Bambu Lab con AMS). La Ender no lo soporta."
        ),
    )
    notes = models.CharField("Notas", max_length=255, blank=True)
    created_at = models.DateTimeField("Creada", auto_now_add=True)

    class Meta:
        verbose_name = "Máquina (impresora)"
        verbose_name_plural = "Máquinas (impresoras)"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductionJob(models.Model):
    """
    Un trabajo de impresión: un producto de un presupuesto (con su cantidad),
    asignado a una máquina y con una posición en la cola de esa máquina.

    Un mismo presupuesto puede tener varios trabajos, repartidos en distintas
    máquinas. La unidad de la cola es el producto, no el presupuesto entero.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", "En cola"
        PRINTING = "PRINTING", "Imprimiendo"
        DONE = "DONE", "Impreso"
        CANCELLED = "CANCELLED", "Cancelado"

    presupuesto = models.ForeignKey(
        "budgets.Presupuesto",
        verbose_name="Presupuesto",
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    producto = models.ForeignKey(
        "budgets.Producto",
        verbose_name="Producto",
        on_delete=models.PROTECT,
        related_name="jobs",
    )
    quantity = models.PositiveIntegerField("Cantidad de piezas", default=1)

    machine = models.ForeignKey(
        Maquina,
        verbose_name="Máquina",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="jobs",
        help_text="Máquina asignada. El sistema recomienda una, podés cambiarla.",
    )
    order = models.PositiveIntegerField(
        "Orden en la cola",
        default=0,
        help_text="Posición dentro de la cola de la máquina (menor = primero).",
    )
    status = models.CharField(
        "Estado", max_length=20, choices=Status.choices, default=Status.PENDING
    )

    # Snapshot del último cálculo de cola (se recalcula al cambiar la cola).
    estimated_start = models.DateTimeField("Inicio estimado", null=True, blank=True)
    estimated_print_end = models.DateTimeField(
        "Fin de impresión estimado", null=True, blank=True
    )

    # Tiempos reales.
    started_at = models.DateTimeField("Inicio real", null=True, blank=True)
    finished_at = models.DateTimeField("Fin real", null=True, blank=True)

    stock_consumed = models.BooleanField(
        "Stock descontado",
        default=False,
        help_text="Se marca solo cuando el trabajo se imprime y se descuenta el material.",
    )

    created_at = models.DateTimeField("Creado", auto_now_add=True)

    class Meta:
        verbose_name = "Trabajo de producción"
        verbose_name_plural = "Trabajos de producción"
        ordering = ["machine", "order", "id"]

    def __str__(self):
        return f"{self.producto} x{self.quantity} ({self.get_status_display()})"

    @property
    def print_hours(self) -> Decimal:
        """Horas de impresión de este trabajo: cantidad × tiempo por pieza."""
        return (
            Decimal(self.quantity) * Decimal(str(self.producto.print_time_hours or 0))
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
        return bool(self.producto and self.producto.is_multicolor)

    def clean(self):
        """Evita asignar una pieza multicolor a una máquina que no lo soporta."""
        from django.core.exceptions import ValidationError

        if (
            self.machine_id
            and self.requires_multicolor
            and not self.machine.supports_multicolor
        ):
            raise ValidationError(
                {
                    "machine": (
                        f"'{self.producto}' es multicolor y '{self.machine}' no "
                        "imprime multicolor. Asigná una máquina con AMS."
                    )
                }
            )

    def consume_stock(self):
        """
        Descuenta del inventario el material que gastó este trabajo (filamento
        × cantidad × merma, y agregados × cantidad) y registra los movimientos.
        Idempotente: solo descuenta una vez (flag stock_consumed). Pensado para
        llamarse cuando el trabajo pasa a 'Impreso'.
        """
        if self.stock_consumed:
            return

        from django.db import transaction
        from django.utils import timezone

        from inventory.models import StockMovement

        producto = self.producto
        with transaction.atomic():
            for line in producto.filament_lines.select_related("filament").all():
                grams = producto.filament_grams_needed(line, self.quantity)
                fil = line.filament
                # deduct_stock no deja el stock en negativo y devuelve el
                # faltante; registramos el movimiento por lo REALMENTE
                # descontado para que el ledger no se desincronice del stock.
                shortage = fil.deduct_stock(grams)
                consumed = grams - shortage
                note = f"Impresión {producto} ×{self.quantity}"
                if shortage > 0:
                    note += f" (faltaron {shortage} g: stock insuficiente)"
                StockMovement.objects.create(
                    filament=fil,
                    quantity=-consumed,
                    reason=StockMovement.Reason.PRODUCTION,
                    related_presupuesto=self.presupuesto,
                    note=note,
                )
            for line in producto.aggregate_lines.select_related("aggregate").all():
                qty = producto.aggregate_qty_needed(line, self.quantity)
                agg = line.aggregate
                shortage = agg.deduct_stock(qty)
                consumed = qty - shortage
                note = f"Impresión {producto} ×{self.quantity}"
                if shortage > 0:
                    note += f" (faltaron {shortage}: stock insuficiente)"
                StockMovement.objects.create(
                    aggregate=agg,
                    quantity=-consumed,
                    reason=StockMovement.Reason.PRODUCTION,
                    related_presupuesto=self.presupuesto,
                    note=note,
                )
            self.stock_consumed = True
            if not self.finished_at:
                self.finished_at = timezone.now()
            self.save(update_fields=["stock_consumed", "finished_at"])


class Tablero(ProductionJob):
    """Proxy para tener en el admin el 'Tablero de producción' (panel general)."""

    class Meta:
        proxy = True
        verbose_name = "Tablero de producción"
        verbose_name_plural = "Tablero de producción"
