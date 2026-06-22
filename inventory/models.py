from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


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
        OTHER = "OTHER", "Otro"

    brand = models.CharField("Marca", max_length=100)
    material_type = models.CharField(
        "Tipo de material", max_length=10, choices=MaterialType.choices
    )
    color = models.CharField("Color", max_length=100)
    color_hex = models.CharField(
        "Color (hex)",
        max_length=7,
        blank=True,
        help_text="Ej: #FF0000. Opcional, para mostrar una muestra de color en el front.",
    )
    cost_per_kg = models.DecimalField(
        "Costo por kg", max_digits=10, decimal_places=2
    )
    stock_grams = models.DecimalField(
        "Stock disponible (g)", max_digits=10, decimal_places=2, default=0
    )
    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Filamento"
        verbose_name_plural = "Filamentos"
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

    def has_enough_stock(self, grams_needed: Decimal) -> bool:
        return self.stock_grams >= grams_needed

    def deduct_stock(self, grams: Decimal) -> Decimal:
        """
        Descuenta `grams` del stock, sin permitir que quede negativo.
        Devuelve la cantidad que efectivamente faltó (0 si había suficiente).
        """
        grams = Decimal(grams)
        if grams <= 0:
            return Decimal("0")

        shortage = max(Decimal("0"), grams - self.stock_grams)
        self.stock_grams = max(Decimal("0"), self.stock_grams - grams)
        self.save(update_fields=["stock_grams", "updated_at"])
        return shortage


class Aggregate(models.Model):
    """
    Insumos que no son filamento: argollas, packaging, llaveros, pegatinas, etc.
    """

    class Category(models.TextChoices):
        HARDWARE = "HARDWARE", "Herraje (argollas, llaveros, etc.)"
        PACKAGING = "PACKAGING", "Packaging"
        DECORATION = "DECORATION", "Decoración (pegatinas, etc.)"
        OTHER = "OTHER", "Otro"

    class Unit(models.TextChoices):
        UNIT = "UNIT", "Unidad"
        PAIR = "PAIR", "Par"
        METER = "METER", "Metro"
        GRAM = "GRAM", "Gramo"

    name = models.CharField("Nombre", max_length=150)
    category = models.CharField(
        "Categoría", max_length=20, choices=Category.choices, default=Category.OTHER
    )
    unit = models.CharField("Unidad", max_length=10, choices=Unit.choices, default=Unit.UNIT)
    cost_per_unit = models.DecimalField(
        "Costo por unidad", max_digits=10, decimal_places=2
    )
    stock_quantity = models.DecimalField(
        "Stock disponible", max_digits=10, decimal_places=2, default=0
    )
    is_active = models.BooleanField("Activo", default=True)
    created_at = models.DateTimeField("Creado", auto_now_add=True)
    updated_at = models.DateTimeField("Actualizado", auto_now=True)

    class Meta:
        verbose_name = "Agregado"
        verbose_name_plural = "Agregados"
        ordering = ["category", "name"]

    def __str__(self):
        return self.name

    def has_enough_stock(self, qty_needed: Decimal) -> bool:
        return self.stock_quantity >= qty_needed

    def deduct_stock(self, qty: Decimal) -> Decimal:
        qty = Decimal(qty)
        if qty <= 0:
            return Decimal("0")

        shortage = max(Decimal("0"), qty - self.stock_quantity)
        self.stock_quantity = max(Decimal("0"), self.stock_quantity - qty)
        self.save(update_fields=["stock_quantity", "updated_at"])
        return shortage


class StockMovement(models.Model):
    """
    Historial de movimientos de stock, tanto de filamento como de agregados.
    Permite auditar por qué bajó o subió el stock de algo.
    """

    class Reason(models.TextChoices):
        PURCHASE = "PURCHASE", "Compra"
        BUDGET_APPROVED = "BUDGET_APPROVED", "Presupuesto aprobado"
        MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT", "Ajuste manual"
        REPRINT_FAILURE = "REPRINT_FAILURE", "Reimpresión por falla"

    filament = models.ForeignKey(
        Filament,
        verbose_name="Filamento",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movements",
    )
    aggregate = models.ForeignKey(
        Aggregate,
        verbose_name="Agregado",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movements",
    )
    quantity = models.DecimalField(
        "Cantidad",
        max_digits=10,
        decimal_places=2,
        help_text="Negativo = salida de stock. Positivo = entrada de stock.",
    )
    reason = models.CharField("Motivo", max_length=20, choices=Reason.choices)
    related_budget = models.ForeignKey(
        "budgets.Budget",
        verbose_name="Presupuesto relacionado",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_movements",
    )
    note = models.CharField("Nota", max_length=255, blank=True)
    created_at = models.DateTimeField("Fecha", auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de stock"
        verbose_name_plural = "Movimientos de stock"
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
                "Un movimiento de stock debe estar vinculado a exactamente "
                "un Filamento o un Agregado (no ambos, no ninguno)."
            )
