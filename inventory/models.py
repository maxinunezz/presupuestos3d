from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _


class Filament(models.Model):
    """
    Representa un 'pool' de filamento: una combinación de marca + tipo de
    material + color, con su precio y stock disponible en gramos.

    No se trackean bobinas individuales: el stock es un total agregado.
    Si en el futuro se necesita trazabilidad por bobina, se puede agregar
    un modelo `FilamentSpool` relacionado sin romper este diseño.
    """

    class MaterialType(models.TextChoices):
        PLA = "PLA", "PLA"
        PETG = "PETG", "PETG"
        ABS = "ABS", "ABS"
        TPU = "TPU", "TPU"
        ASA = "ASA", "ASA"
        NYLON = "NYLON", "Nylon"
        OTHER = "OTHER", _("Otro")

    brand = models.CharField(_("Marca"), max_length=100)
    material_type = models.CharField(
        _("Tipo de material"), max_length=10, choices=MaterialType.choices
    )
    color = models.CharField(_("Color"), max_length=100)
    color_hex = models.CharField(
        _("Color (hex)"),
        max_length=7,
        blank=True,
        help_text=_("Ej: #FF0000. Opcional, para mostrar una muestra de color en el front."),
    )
    cost_per_kg = models.DecimalField(
        _("Costo por kg"), max_digits=10, decimal_places=2
    )
    stock_grams = models.DecimalField(
        _("Stock disponible (g)"), max_digits=10, decimal_places=2, default=0
    )
    min_stock = models.DecimalField(
        _("Stock mínimo (g)"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("1000"),
        help_text=_(
            "Si el stock baja de este valor, salta la alerta de bajo stock "
            "(la campanita). En gramos. Ej: 1000 = 1 kg. Poné 0 para no avisar "
            "de este filamento."
        ),
    )
    is_active = models.BooleanField(_("Activo"), default=True)
    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado"), auto_now=True)

    class Meta:
        verbose_name = _("Filamento")
        verbose_name_plural = _("Filamentos")
        ordering = ["brand", "material_type", "color"]
        constraints = [
            models.UniqueConstraint(
                fields=["brand", "material_type", "color"],
                name="unique_filament_brand_material_color",
            )
        ]

    def __str__(self):
        return f"{self.brand} {self.material_type} {self.color}"

    @property
    def cost_per_gram(self) -> Decimal:
        return (self.cost_per_kg / Decimal("1000")).quantize(Decimal("0.0001"))

    @property
    def is_low_stock(self) -> bool:
        """True si el stock está por debajo del mínimo configurado (>0)."""
        return self.min_stock > 0 and self.stock_grams < self.min_stock

    def has_enough_stock(self, grams_needed: Decimal) -> bool:
        return self.stock_grams >= grams_needed

    def deduct_stock(self, grams: Decimal, allow_negative: bool = False) -> Decimal:
        """
        Descuenta `grams` del stock. Devuelve cuánto faltó (0 si alcanzaba).

        - `allow_negative=False` (default): no deja el stock negativo (lo deja
          en cero). Pensado para ajustes/compras.
        - `allow_negative=True`: descuenta los `grams` completos aunque el stock
          quede en negativo. Lo usa la PRODUCCIÓN al aprobar un pedido, así el
          stock negativo muestra de forma persistente cuánto hay que comprar y
          la reversa por cancelación puede devolver lo justo.
        """
        grams = Decimal(grams)
        if grams <= 0:
            return Decimal("0")

        shortage = max(Decimal("0"), grams - self.stock_grams)
        if allow_negative:
            self.stock_grams = self.stock_grams - grams
        else:
            self.stock_grams = max(Decimal("0"), self.stock_grams - grams)
        self.save(update_fields=["stock_grams", "updated_at"])
        return shortage


class Aggregate(models.Model):
    """
    Insumos que no son filamento: argollas, packaging, llaveros, pegatinas, etc.
    """

    class Category(models.TextChoices):
        HARDWARE = "HARDWARE", _("Herraje (argollas, llaveros, etc.)")
        PACKAGING = "PACKAGING", "Packaging"
        DECORATION = "DECORATION", _("Decoración (pegatinas, etc.)")
        OTHER = "OTHER", _("Otro")

    class Unit(models.TextChoices):
        UNIT = "UNIT", _("Unidad")
        PAIR = "PAIR", _("Par")
        METER = "METER", _("Metro")
        GRAM = "GRAM", _("Gramo")

    name = models.CharField(_("Nombre"), max_length=150)
    category = models.CharField(
        _("Categoría"), max_length=20, choices=Category.choices, default=Category.OTHER
    )
    unit = models.CharField(_("Unidad"), max_length=10, choices=Unit.choices, default=Unit.UNIT)
    cost_per_unit = models.DecimalField(
        _("Costo por unidad"), max_digits=10, decimal_places=2
    )
    stock_quantity = models.DecimalField(
        _("Stock disponible"), max_digits=10, decimal_places=2, default=0
    )
    min_stock = models.DecimalField(
        _("Stock mínimo"),
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_(
            "Si el stock baja de este valor, salta la alerta de bajo stock "
            "(la campanita). Va en la MISMA unidad del agregado: si se mide en "
            "unidades, poné unidades (ej: pelotas → 20); si se mide en gramos, "
            "poné gramos (ej: argollas → 200). Poné 0 para no avisar de este agregado."
        ),
    )
    is_active = models.BooleanField(_("Activo"), default=True)
    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado"), auto_now=True)

    class Meta:
        verbose_name = _("Agregado")
        verbose_name_plural = _("Agregados")
        ordering = ["category", "name"]

    def __str__(self):
        return self.name

    @property
    def is_low_stock(self) -> bool:
        """True si el stock está por debajo del mínimo configurado (>0)."""
        return self.min_stock > 0 and self.stock_quantity < self.min_stock

    def has_enough_stock(self, qty_needed: Decimal) -> bool:
        return self.stock_quantity >= qty_needed

    def deduct_stock(self, qty: Decimal, allow_negative: bool = False) -> Decimal:
        """
        Descuenta `qty` del stock. Devuelve cuánto faltó (0 si alcanzaba).
        Con `allow_negative=True` descuenta todo aunque quede en negativo (lo
        usa la producción al aprobar; ver Filament.deduct_stock).
        """
        qty = Decimal(qty)
        if qty <= 0:
            return Decimal("0")

        shortage = max(Decimal("0"), qty - self.stock_quantity)
        if allow_negative:
            self.stock_quantity = self.stock_quantity - qty
        else:
            self.stock_quantity = max(Decimal("0"), self.stock_quantity - qty)
        self.save(update_fields=["stock_quantity", "updated_at"])
        return shortage


class StockTotals(Filament):
    """
    Modelo 'proxy' (no crea tabla nueva) que sirve solo para tener una página
    propia en el admin: 'Totales de inventario'. La vista real se arma en el
    admin combinando Filamentos y Agregados con buscador y totales.
    """

    class Meta:
        proxy = True
        verbose_name = _("Totales de inventario")
        verbose_name_plural = _("Totales de inventario")


class StockMovement(models.Model):
    """
    Historial de movimientos de stock, tanto de filamento como de agregados.
    Permite auditar por qué bajó o subió el stock de algo.
    """

    class Reason(models.TextChoices):
        PURCHASE = "PURCHASE", _("Compra")
        BUDGET_APPROVED = "BUDGET_APPROVED", _("Presupuesto aprobado")
        PRODUCTION = "PRODUCTION", _("Producción (impresión)")
        BUDGET_CANCELLED = "BUDGET_CANCELLED", _("Cancelación de presupuesto")
        MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT", _("Ajuste manual")
        REPRINT_FAILURE = "REPRINT_FAILURE", _("Reimpresión por falla")

    filament = models.ForeignKey(
        Filament,
        verbose_name=_("Filamento"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    aggregate = models.ForeignKey(
        Aggregate,
        verbose_name=_("Agregado"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    quantity = models.DecimalField(
        _("Cantidad"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Negativo = salida de stock. Positivo = entrada de stock."),
    )
    reason = models.CharField(_("Motivo"), max_length=20, choices=Reason.choices)
    related_presupuesto = models.ForeignKey(
        "budgets.Presupuesto",
        verbose_name=_("Presupuesto relacionado"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
    )
    note = models.CharField(_("Nota"), max_length=255, blank=True)
    created_at = models.DateTimeField(_("Fecha"), auto_now_add=True)

    class Meta:
        verbose_name = _("Movimiento de stock")
        verbose_name_plural = _("Movimientos de stock")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(filament__isnull=False, aggregate__isnull=True)
                    | models.Q(filament__isnull=True, aggregate__isnull=False)
                ),
                name="stock_movement_exactly_one_item",
            )
        ]

    def __str__(self):
        item = self.filament or self.aggregate
        return f"{item} | {self.quantity} | {self.get_reason_display()}"

    def clean(self):
        if bool(self.filament) == bool(self.aggregate):
            raise ValidationError(
                gettext(
                    "Un movimiento de stock debe estar vinculado a exactamente "
                    "un Filamento o un Agregado (no ambos, no ninguno)."
                )
            )


class AjusteStock(StockMovement):
    """
    Modelo proxy de StockMovement para tener en el admin una sección clara de
    'Ajuste manual de stock'. No crea tabla nueva: cada ajuste se guarda como
    un StockMovement con motivo Ajuste manual, y el admin se encarga de aplicar
    la diferencia al stock del artículo.
    """

    class Meta:
        proxy = True
        verbose_name = _("Ajuste manual de stock")
        verbose_name_plural = _("Ajustes manuales de stock")


class CompraNotConfirmableError(Exception):
    """
    Se intentó confirmar una compra que no está en estado Borrador.
    Evita sumar el stock dos veces sobre la misma compra.
    """


class Compra(models.Model):
    """
    Una compra de insumos. Agrupa varias líneas (cada una de un filamento o
    un agregado, existente o nuevo). Al confirmarla, suma el stock comprado,
    actualiza el precio de cada artículo y registra los movimientos de stock.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Borrador")
        CONFIRMED = "CONFIRMED", _("Confirmada")

    supplier = models.CharField(_("Proveedor"), max_length=150, blank=True)
    invoice_number = models.CharField(_("N° de factura / remito"), max_length=100, blank=True)
    notes = models.TextField(_("Notas"), blank=True)

    status = models.CharField(
        _("Estado"), max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    confirmed_at = models.DateTimeField(_("Confirmada el"), null=True, blank=True)

    created_at = models.DateTimeField(_("Creada"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizada"), auto_now=True)

    class Meta:
        verbose_name = _("Compra")
        verbose_name_plural = _("Compras")
        ordering = ["-created_at"]

    def __str__(self):
        proveedor = self.supplier or gettext("sin proveedor")
        return f"#{self.pk} {proveedor} ({self.get_status_display()})"

    @property
    def total(self) -> Decimal:
        """Costo total de la compra: suma de todas las líneas."""
        return sum((line.line_cost for line in self.lines.all()), Decimal("0"))

    def confirm(self):
        """
        Pasa la compra a CONFIRMED: por cada línea suma el stock comprado,
        actualiza el precio del artículo (si se cargó uno) y registra un
        StockMovement (motivo Compra). Todo dentro de una transacción.

        Solo se puede confirmar una compra en estado Borrador, para no sumar
        el stock dos veces sobre la misma compra.
        """
        if self.status != Compra.Status.DRAFT:
            raise CompraNotConfirmableError(
                gettext(
                    "No se puede confirmar la compra #%(pk)s: su estado es "
                    "'%(status)s'. Solo se pueden confirmar "
                    "compras en estado Borrador."
                )
                % {"pk": self.pk, "status": self.get_status_display()}
            )

        with transaction.atomic():
            for line in self.lines.select_related("filament", "aggregate").all():
                line.apply_to_inventory()

            self.status = Compra.Status.CONFIRMED
            self.confirmed_at = timezone.now()
            self.save(update_fields=["status", "confirmed_at", "updated_at"])


class CompraLine(models.Model):
    """
    Una línea de compra: un filamento o un agregado (existente o creado en el
    momento desde el selector), con la cantidad comprada y el precio pagado.

    - Filamento: `quantity` en gramos, `unit_price` = costo por kg.
    - Agregado:  `quantity` en unidades, `unit_price` = costo por unidad.

    Si `unit_price` se deja vacío, se mantiene el precio actual del artículo.
    """

    compra = models.ForeignKey(
        Compra, on_delete=models.CASCADE, related_name="lines"
    )
    filament = models.ForeignKey(
        Filament,
        verbose_name=_("Filamento"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="compra_lines",
    )
    aggregate = models.ForeignKey(
        Aggregate,
        verbose_name=_("Agregado"),
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="compra_lines",
    )
    quantity = models.DecimalField(
        _("Cantidad comprada"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Filamento: en gramos. Agregado: en unidades."),
    )
    unit_price = models.DecimalField(
        _("Precio pagado"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Filamento: costo por kg. Agregado: costo por unidad. Si se deja "
            "vacío, se mantiene el precio actual del artículo."
        ),
    )

    class Meta:
        verbose_name = _("Línea de compra")
        verbose_name_plural = _("Líneas de compra")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(filament__isnull=False, aggregate__isnull=True)
                    | models.Q(filament__isnull=True, aggregate__isnull=False)
                ),
                name="compra_line_exactly_one_item",
            )
        ]

    def __str__(self):
        item = self.filament or self.aggregate
        return f"{item} x{self.quantity}"

    def clean(self):
        if bool(self.filament) == bool(self.aggregate):
            raise ValidationError(
                gettext(
                    "Una línea de compra debe estar vinculada a exactamente un "
                    "Filamento o un Agregado (no ambos, no ninguno)."
                )
            )

    @property
    def item(self):
        return self.filament or self.aggregate

    @property
    def effective_unit_price(self) -> Decimal:
        """Precio a usar: el cargado o, si está vacío, el precio actual del artículo."""
        if self.unit_price is not None:
            return self.unit_price
        if self.filament_id:
            return self.filament.cost_per_kg
        return self.aggregate.cost_per_unit

    @property
    def line_cost(self) -> Decimal:
        """
        Costo total de la línea. Para filamento, el precio es por kg y la
        cantidad en gramos, así que se convierte a kg.
        """
        price = self.effective_unit_price
        if self.filament_id:
            return (self.quantity / Decimal("1000") * price).quantize(Decimal("0.01"))
        return (self.quantity * price).quantize(Decimal("0.01"))

    def apply_to_inventory(self):
        """
        Suma la cantidad comprada al stock del artículo, actualiza su precio
        si se cargó uno nuevo, y registra el StockMovement correspondiente.
        Pensado para llamarse desde Compra.confirm() dentro de una transacción.
        """
        if self.filament_id:
            fil = self.filament
            fil.stock_grams = (fil.stock_grams + self.quantity).quantize(
                Decimal("0.01")
            )
            note = ""
            if self.unit_price is not None and self.unit_price != fil.cost_per_kg:
                note = f"Precio actualizado: ${fil.cost_per_kg}/kg → ${self.unit_price}/kg"
                fil.cost_per_kg = self.unit_price
            fil.save(update_fields=["stock_grams", "cost_per_kg", "updated_at"])
            StockMovement.objects.create(
                filament=fil,
                quantity=self.quantity,
                reason=StockMovement.Reason.PURCHASE,
                note=note,
            )
        else:
            agg = self.aggregate
            agg.stock_quantity = (agg.stock_quantity + self.quantity).quantize(
                Decimal("0.01")
            )
            note = ""
            if self.unit_price is not None and self.unit_price != agg.cost_per_unit:
                note = f"Precio actualizado: ${agg.cost_per_unit}/u → ${self.unit_price}/u"
                agg.cost_per_unit = self.unit_price
            agg.save(update_fields=["stock_quantity", "cost_per_unit", "updated_at"])
            StockMovement.objects.create(
                aggregate=agg,
                quantity=self.quantity,
                reason=StockMovement.Reason.PURCHASE,
                note=note,
            )
