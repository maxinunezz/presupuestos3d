import math
from decimal import ROUND_HALF_UP, Decimal

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

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

    class Priority(models.IntegerChoices):
        ALTA = 1, _("Alta")
        MEDIA = 2, _("Media")
        BAJA = 3, _("Baja")
        SIN = 9, _("Sin prioridad")

    name = models.CharField(_("Nombre / pieza"), max_length=200)
    description = models.TextField(_("Descripción"), blank=True)

    priority = models.PositiveSmallIntegerField(
        _("Prioridad en la cola"),
        choices=Priority.choices,
        default=Priority.SIN,
        help_text=_(
            "Define en qué orden entra a la cola de producción: prioridad más "
            "alta se imprime antes. 'Sin prioridad' va al final de la cola."
        ),
    )

    is_multicolor = models.BooleanField(
        _("Multicolor (AMS)"),
        default=False,
        help_text=_(
            "Marcá si la pieza usa varios colores/filamentos en simultáneo. "
            "Solo se puede imprimir en máquinas con AMS (ej. Bambu Lab), no en la Ender."
        ),
    )

    # --- Impresión y máquina ---
    # Las horas de máquina y el filamento ya NO viven acá: ahora están en cada
    # Pieza (ver modelo Pieza). El producto suma las piezas para sus totales.
    machine_cost_per_hour = models.DecimalField(
        _("Costo de máquina por hora"), max_digits=10, decimal_places=2, default=0
    )
    waste_percent = models.DecimalField(
        _("Merma de material (%)"),
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=_(
            "Desperdicio de filamento por purga (multicolor), soportes y fallas. "
            "Se aplica sobre el costo y el consumo de material."
        ),
    )

    # --- Mano de obra / post-proceso ---
    post_processing_hours = models.DecimalField(
        _("Post-proceso por pieza (hs)"),
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text=_("Tiempo de armado, lijado, pintado, pegado de agregados, etc."),
    )
    labor_cost_per_hour = models.DecimalField(
        _("Costo de mano de obra por hora"), max_digits=10, decimal_places=2, default=0
    )

    # --- Precio ---
    margin_percent = models.DecimalField(
        _("Margen (%)"), max_digits=5, decimal_places=2, default=0
    )
    round_to = models.DecimalField(
        _("Redondear precio a múltiplo de"),
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text=_("Ej: 100 redondea el precio a la centena más cercana. 0 = sin redondeo."),
    )

    # --- Archivo del modelo (solo local por ahora) ---
    gcode = models.TextField(
        _("G-code"),
        blank=True,
        help_text=_("Pegá acá el g-code del laminador (opcional)."),
    )
    model_file = models.FileField(
        _("Archivo .3mf / modelo"),
        upload_to="productos/",
        blank=True,
        help_text=_("Subí el .3mf o archivo del modelo (opcional, solo desarrollo local)."),
    )

    # --- Stock de productos terminados ---
    stock_quantity = models.PositiveIntegerField(
        _("Stock de productos terminados"),
        default=0,
        help_text=_(
            "Unidades de este producto ya terminadas (armadas) y disponibles "
            "para entregar sin imprimir. Sube al completar un pedido de stock."
        ),
    )
    min_stock = models.PositiveIntegerField(
        _("Stock mínimo de terminados"),
        default=0,
        help_text=_(
            "Cuántas unidades terminadas querés tener siempre en stock. Si el "
            "stock baja de este número, el producto aparece como 'a reponer'. "
            "0 = no se controla el mínimo."
        ),
    )

    is_active = models.BooleanField(_("Activo"), default=True)
    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado"), auto_now=True)

    class Meta:
        verbose_name = _("Costeo de producto")
        verbose_name_plural = _("Costeo de productos")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_low_stock(self) -> bool:
        """True si se controla un mínimo y el stock de terminados está por debajo."""
        return self.min_stock > 0 and self.stock_quantity < self.min_stock

    @property
    def stock_to_make(self) -> int:
        """Unidades terminadas que faltan para llegar al mínimo (0 si está ok)."""
        return max(self.min_stock - self.stock_quantity, 0)

    # ---- Costos calculados (todos por UNA unidad) ----

    @property
    def waste_multiplier(self) -> Decimal:
        return Decimal("1") + (self.waste_percent / Decimal("100"))

    # ---- Totales de las piezas (sumatoria de todas las piezas del producto) ----

    @property
    def total_filament_grams(self) -> Decimal:
        """Gramos de filamento de UN producto: suma de todas sus piezas."""
        return sum(
            (pieza.filament_grams for pieza in self.piezas.all()), Decimal("0")
        ).quantize(Decimal("0.01"))

    @property
    def total_machine_hours(self) -> Decimal:
        """Horas de máquina de UN producto: suma de todas sus piezas."""
        return sum(
            (pieza.machine_hours for pieza in self.piezas.all()), Decimal("0")
        ).quantize(Decimal("0.01"))

    @property
    def print_time_hours(self) -> Decimal:
        """
        Compatibilidad: horas de impresión de UN producto. Antes era un campo;
        ahora se calcula sumando las piezas. Se mantiene el nombre para no romper
        el cálculo de la cola de producción.
        """
        return self.total_machine_hours

    @property
    def needs_ams(self) -> bool:
        """True si alguna pieza del producto necesita AMS (multicolor)."""
        return any(pieza.requires_ams for pieza in self.piezas.all())

    @property
    def material_cost(self) -> Decimal:
        """Costo de filamento de UN producto, sin merma (suma de las piezas)."""
        return sum(
            (pieza.material_cost for pieza in self.piezas.all()), Decimal("0")
        ).quantize(Decimal("0.01"))

    @property
    def material_waste_cost(self) -> Decimal:
        """Costo del desperdicio de material de UN producto."""
        return (self.material_cost * (self.waste_percent / Decimal("100"))).quantize(
            Decimal("0.01")
        )

    @property
    def aggregate_cost(self) -> Decimal:
        """Costo de agregados de UN producto."""
        return sum(
            (line.line_cost for line in self.aggregate_lines.all()), Decimal("0")
        )

    @property
    def machine_cost(self) -> Decimal:
        return (self.total_machine_hours * _dec(self.machine_cost_per_hour)).quantize(
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

    # ---- Stock (consumo para una cantidad dada de productos) ----

    def aggregated_filament(self):
        """
        Junta el filamento de TODAS las piezas del producto y devuelve una lista
        de (Filament, gramos_por_producto_sin_merma), sumando por filamento.
        Cada pieza aporta gramos_por_corrida × corridas_de_gcode.
        """
        from collections import defaultdict

        grams = defaultdict(lambda: Decimal("0"))
        objs = {}
        for pieza in self.piezas.all():
            runs = pieza.gcode_runs
            for line in pieza.filament_lines.all():
                grams[line.filament_id] += _dec(line.grams_used) * runs
                objs[line.filament_id] = line.filament
        return [(objs[fid], total) for fid, total in grams.items()]

    def filament_grams_needed(self, grams_per_product, quantity) -> Decimal:
        """Gramos reales para `quantity` productos: gramos/producto × cantidad × merma."""
        return (
            _dec(grams_per_product) * quantity * self.waste_multiplier
        ).quantize(Decimal("0.01"))

    def aggregate_qty_needed(self, line, quantity) -> Decimal:
        """Unidades reales que consume una línea de agregado: por producto × cantidad."""
        return (line.quantity * quantity).quantize(Decimal("0.01"))


class ProductoAggregateLine(models.Model):
    """Una línea de agregado (insumo no-filamento) que entra en UNA unidad."""

    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, related_name="aggregate_lines"
    )
    aggregate = models.ForeignKey(
        Aggregate, on_delete=models.PROTECT, related_name="producto_lines"
    )
    quantity = models.DecimalField(_("Cantidad"), max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(
        _("Costo por unidad (congelado)"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Costo por unidad guardado al costear. Si se deja vacío, se toma "
            "el precio actual del agregado."
        ),
    )

    class Meta:
        verbose_name = _("Línea de agregado")
        verbose_name_plural = _("Líneas de agregado")

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


class Pieza(models.Model):
    """
    Una pieza física que compone un Producto. Un producto puede necesitar
    varias piezas (ej. una maceta = base + tapa). Cada pieza tiene sus propias
    líneas de filamento y sus horas de máquina, cargadas POR CORRIDA DE GCODE
    (una impresión que puede sacar varias piezas a la vez).

    Para producir UN producto hacen falta `units_needed` unidades de esta pieza;
    si el gcode saca `pieces_per_gcode` por corrida, el sistema calcula cuántas
    corridas hacen falta = ceil(units_needed / pieces_per_gcode) y multiplica el
    filamento y las horas por esa cantidad de corridas.
    """

    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, related_name="piezas"
    )
    name = models.CharField(_("Nombre de la pieza"), max_length=200)
    units_needed = models.PositiveIntegerField(
        _("Unidades necesarias por producto"),
        default=1,
        help_text=_("Cuántas unidades de esta pieza lleva UN producto."),
    )
    pieces_per_gcode = models.PositiveIntegerField(
        _("Piezas por corrida de gcode"),
        default=1,
        help_text=_("Cuántas unidades de esta pieza salen en UNA impresión (un gcode)."),
    )
    print_time_hours = models.DecimalField(
        _("Horas de máquina por corrida de gcode"),
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text=_("Tiempo de impresión de UNA corrida del gcode (saca `piezas por gcode`)."),
    )
    requires_ams = models.BooleanField(
        _("Necesita AMS (multicolor)"),
        default=False,
        help_text=_(
            "Se marca solo cuando la pieza usa más de una línea de filamento "
            "(multicolor): debe ir a una máquina con AMS. Podés forzarlo a mano."
        ),
    )
    stock_quantity = models.PositiveIntegerField(
        _("Stock de piezas impresas"),
        default=0,
        help_text=_("Unidades de esta pieza ya impresas y disponibles en stock."),
    )
    order = models.PositiveIntegerField(_("Orden"), default=0)

    class Meta:
        verbose_name = _("Pieza")
        verbose_name_plural = _("Piezas")
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.name} (×{self.units_needed})"

    # ---- Cálculos por UN producto ----

    @property
    def gcode_runs(self) -> int:
        """Corridas de gcode para UN producto: ceil(unidades / piezas por gcode)."""
        ppg = self.pieces_per_gcode or 1
        units = self.units_needed or 0
        if units <= 0:
            return 0
        return math.ceil(units / ppg)

    @property
    def filament_grams_per_run(self) -> Decimal:
        """Gramos de filamento de UNA corrida del gcode (sin merma)."""
        return sum(
            (_dec(line.grams_used) for line in self.filament_lines.all()), Decimal("0")
        )

    @property
    def filament_grams(self) -> Decimal:
        """Gramos de filamento para UN producto (corridas × gramos por corrida)."""
        return (self.filament_grams_per_run * self.gcode_runs).quantize(Decimal("0.01"))

    @property
    def machine_hours(self) -> Decimal:
        """Horas de máquina para UN producto (corridas × horas por corrida)."""
        return (_dec(self.print_time_hours) * self.gcode_runs).quantize(Decimal("0.01"))

    @property
    def material_cost_per_run(self) -> Decimal:
        """Costo de filamento de UNA corrida (sin merma)."""
        return sum(
            (line.line_cost for line in self.filament_lines.all()), Decimal("0")
        )

    @property
    def material_cost(self) -> Decimal:
        """Costo de filamento para UN producto (corridas × costo por corrida)."""
        return (self.material_cost_per_run * self.gcode_runs).quantize(Decimal("0.01"))

    def auto_requires_ams(self) -> bool:
        """True si la pieza tiene más de una línea de filamento (multicolor)."""
        return self.filament_lines.count() > 1


class PiezaFilamentLine(models.Model):
    """Una línea de filamento de UNA corrida de gcode de una pieza."""

    pieza = models.ForeignKey(
        Pieza, on_delete=models.CASCADE, related_name="filament_lines"
    )
    filament = models.ForeignKey(
        Filament, on_delete=models.PROTECT, related_name="pieza_lines"
    )
    grams_used = models.DecimalField(
        _("Gramos usados (por corrida)"), max_digits=10, decimal_places=2
    )
    unit_cost = models.DecimalField(
        _("Costo por gramo (congelado)"),
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=_(
            "Costo por gramo guardado al momento de costear. Si se deja vacío, "
            "se toma el precio actual del filamento."
        ),
    )

    class Meta:
        verbose_name = _("Línea de filamento (pieza)")
        verbose_name_plural = _("Líneas de filamento (pieza)")

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


class StockPiezas(Pieza):
    """
    Proxy de Pieza para tener en el admin una página 'Stock de piezas': lista
    las piezas de cada producto con su stock de unidades ya impresas. El stock
    sube cuando se imprimen piezas y baja cuando se confirma un pedido que las usa.
    """

    class Meta:
        proxy = True
        verbose_name = _("Stock de piezas")
        verbose_name_plural = _("Stock de piezas")


class StockProductos(Producto):
    """
    Proxy de Producto para tener en el admin una página 'Stock de productos
    terminados': lista los productos con su stock de unidades ya armadas y su
    stock mínimo. El stock sube al completar un pedido marcado 'para stock' y
    baja al aprobar pedidos de cliente que lo consumen. Se puede ajustar a mano.
    """

    class Meta:
        proxy = True
        verbose_name = _("Stock de productos terminados")
        verbose_name_plural = _("Stock de productos terminados")


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
        DRAFT = "DRAFT", _("Borrador")
        SENT = "SENT", _("Enviado")
        APPROVED = "APPROVED", _("Aprobado")
        IN_PRODUCTION = "IN_PRODUCTION", _("En producción")
        COMPLETED = "COMPLETED", _("Completado")
        CANCELLED = "CANCELLED", _("Cancelado")

    client_name = models.CharField(_("Cliente"), max_length=150)
    description = models.TextField(_("Notas / descripción"), blank=True)

    para_stock = models.BooleanField(
        _("Pedido para reponer stock (sin cliente)"),
        default=False,
        help_text=_(
            "Marcá si es producción para tu stock interno, no para un cliente. "
            "Funciona igual que un pedido normal (se aprueba, se imprime, se "
            "completa); al completarse, las unidades terminadas suman al stock "
            "de productos terminados en vez de entregarse."
        ),
    )

    fixed_cost = models.DecimalField(
        _("Costo fijo por pedido"),
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text=_("Costo que se cobra una sola vez por pedido (setup, envío, etc.)."),
    )
    round_to = models.DecimalField(
        _("Redondear total a múltiplo de"),
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text=_("Ej: 100 redondea el total a la centena más cercana. 0 = sin redondeo."),
    )

    status = models.CharField(
        _("Estado"), max_length=20, choices=Status.choices, default=Status.DRAFT
    )

    # --- Fechas por estado (reloj de producción) ---
    sent_at = models.DateTimeField(_("Enviado el"), null=True, blank=True)
    approved_at = models.DateTimeField(_("Aprobado el"), null=True, blank=True)
    production_started_at = models.DateTimeField(
        _("Producción iniciada el"), null=True, blank=True
    )
    production_finished_at = models.DateTimeField(
        _("Producción terminada el"), null=True, blank=True
    )
    completed_at = models.DateTimeField(_("Completado el"), null=True, blank=True)

    # --- Entrega ---
    due_date = models.DateTimeField(
        _("Fecha de entrega"),
        null=True,
        blank=True,
        help_text=_(
            "Se calcula sola con la cola de producción. Podés pisarla a mano: "
            "si la editás, queda fija y deja de recalcularse."
        ),
    )
    due_date_is_manual = models.BooleanField(
        _("Entrega fijada a mano"), default=False
    )

    stock_provisioned = models.BooleanField(
        _("Inventario descontado"),
        default=False,
        help_text=_(
            "Se marca al aprobar: ya se descontaron las piezas de stock, el "
            "filamento y los agregados. Evita descontar dos veces."
        ),
    )
    stock_reversed = models.BooleanField(
        _("Inventario devuelto"),
        default=False,
        help_text=_(
            "Se marca al cancelar un pedido aprobado: ya se devolvió al stock el "
            "material de lo que no se imprimió, las piezas y los agregados. "
            "Evita devolver dos veces."
        ),
    )
    finished_stock_added = models.BooleanField(
        _("Sumado al stock de terminados"),
        default=False,
        help_text=_(
            "Se marca al completar un pedido 'para stock': ya se sumaron sus "
            "unidades terminadas al stock de productos. Evita sumar dos veces."
        ),
    )

    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado"), auto_now=True)

    class Meta:
        verbose_name = _("Presupuesto")
        verbose_name_plural = _("Presupuestos")
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

    @property
    def is_ready_to_deliver(self) -> bool:
        """
        Pedido APROBADO de CLIENTE que se sirvió entero del stock (de productos
        terminados o de piezas) y por eso no generó ningún trabajo de producción:
        no hay nada que imprimir, está listo para entregar y marcar como
        Completado. Los pedidos para_stock no aplican (no se entregan: su
        terminado va al stock interno al completarse).
        """
        return (
            self.status == Presupuesto.Status.APPROVED
            and self.stock_provisioned
            and not self.para_stock
            and not self.jobs.exists()
        )

    # ---- Producción / tiempos ----

    @property
    def total_print_hours(self) -> Decimal:
        """Horas de impresión del pedido: Σ productos × cantidad × horas/producto."""
        return sum(
            (
                Decimal(item.quantity) * _dec(item.producto.total_machine_hours)
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

    def _provision_production(self):
        """
        Al APROBAR el pedido (= confirmarlo) hace de una sola vez todo el trabajo
        de inventario y cola, de forma idempotente (no descuenta dos veces):

          1. Recorre cada pieza que necesita cada producto. Lo que haya en STOCK
             DE PIEZAS se descuenta y NO se imprime; solo se manda a la cola lo
             que falta imprimir.
          2. Crea un trabajo de producción por cada pieza a imprimir, asignado a
             una máquina (respetando AMS), y descuenta su FILAMENTO del inventario
             ahí mismo (gramos por corrida × corridas × merma).
          3. Descuenta los AGREGADOS a nivel producto (× cantidad).

        El material se descuenta acá, al aprobar (no al imprimir), así las
        métricas de inventario y costos lo reflejan apenas se confirma el pedido.

        Devuelve {"from_stock": [...], "shortages": [...]}:
          - from_stock: piezas que salieron de stock (no se imprimen).
          - shortages: insumos cuyo stock no alcanzó al descontar.
        """
        from production.models import ProductionJob
        from production.scheduler import machine_free_times, recommend_machine

        from inventory.models import StockMovement

        result = {"from_stock": [], "from_finished": [], "shortages": []}
        if self.stock_provisioned:
            return result

        free = machine_free_times()

        # 0) Pedido de CLIENTE: primero servimos del stock de PRODUCTOS TERMINADOS
        #    (ya armados). Lo que sale de ahí no se produce ni consume piezas,
        #    material o agregados (esos ya se gastaron cuando se fabricó el stock).
        #    Los pedidos 'para stock' nunca consumen su propio stock: producen.
        produce_qty = {}  # item.id -> unidades que sí hay que producir
        for item in self.items.select_related("producto").all():
            qty = item.quantity
            if not self.para_stock and qty > 0:
                from_finished = min(item.producto.stock_quantity, qty)
                if from_finished > 0:
                    Producto.objects.filter(pk=item.producto_id).update(
                        stock_quantity=models.F("stock_quantity") - from_finished
                    )
                    item.from_finished_stock = from_finished
                    item.save(update_fields=["from_finished_stock"])
                    qty -= from_finished
                    result["from_finished"].append(
                        {"producto": str(item.producto), "units": from_finished}
                    )
            produce_qty[item.id] = qty

        # 1) Planificamos qué imprimir, descontando primero el stock de piezas.
        plan = []  # (item, pieza, unidades_a_imprimir)
        for item in self.items.select_related("producto").all():
            item_qty = produce_qty[item.id]
            if item_qty <= 0:
                continue
            for pieza in item.producto.piezas.all():
                units_required = pieza.units_needed * item_qty
                if units_required <= 0:
                    continue
                from_stock = min(pieza.stock_quantity, units_required)
                if from_stock > 0:
                    Pieza.objects.filter(pk=pieza.pk).update(
                        stock_quantity=models.F("stock_quantity") - from_stock
                    )
                    result["from_stock"].append(
                        {
                            "pieza": pieza.name,
                            "producto": str(item.producto),
                            "units": from_stock,
                        }
                    )
                to_print = units_required - from_stock
                if to_print > 0:
                    plan.append((item, pieza, to_print))

        # 2) Creamos los trabajos (los más largos primero, para balancear las
        #    colas) y descontamos el filamento de cada uno.
        def _job_hours(pieza, to_print):
            runs = math.ceil(to_print / (pieza.pieces_per_gcode or 1))
            return _dec(pieza.print_time_hours) * runs

        # Prioridad manual del producto primero (1=Alta … 9=Sin prioridad va al
        # final); a igual prioridad, el trabajo más largo se asigna antes.
        plan.sort(key=lambda t: (t[0].producto.priority, -_job_hours(t[1], t[2])))

        for item, pieza, to_print in plan:
            print_hours = _job_hours(pieza, to_print)
            machine, free = recommend_machine(
                print_hours,
                _free_cache=free,
                requires_multicolor=pieza.requires_ams,
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
            job = ProductionJob.objects.create(
                presupuesto=self,
                producto=item.producto,
                pieza=pieza,
                quantity=to_print,
                machine=machine,
                order=order,
            )
            # Descuenta el filamento de esta pieza ahora mismo (al aprobar).
            result["shortages"].extend(job.consume_stock())

        # 3) Agregados a nivel producto (× lo que se PRODUCE, no lo servido del
        #    stock de terminados, que ya traía sus agregados).
        for item in self.items.select_related("producto").all():
            item_qty = produce_qty[item.id]
            if item_qty <= 0:
                continue
            producto = item.producto
            for line in producto.aggregate_lines.select_related("aggregate").all():
                qty = producto.aggregate_qty_needed(line, item_qty)
                if qty <= 0:
                    continue
                agg = line.aggregate
                shortage = agg.deduct_stock(qty, allow_negative=True)
                note = gettext("Pedido #%(pk)s: %(producto)s ×%(qty)s") % {
                    "pk": self.pk,
                    "producto": producto,
                    "qty": item.quantity,
                }
                if shortage > 0:
                    note += gettext(" (faltaron %(shortage)s: quedó en negativo)") % {
                        "shortage": shortage
                    }
                    result["shortages"].append({"item": str(agg), "missing": shortage})
                StockMovement.objects.create(
                    aggregate=agg,
                    quantity=-qty,
                    reason=StockMovement.Reason.PRODUCTION,
                    related_presupuesto=self,
                    note=note,
                )

        self.stock_provisioned = True
        self.save(update_fields=["stock_provisioned", "updated_at"])
        return result

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
            for filament, grams_per_product in producto.aggregated_filament():
                needed = producto.filament_grams_needed(grams_per_product, item.quantity)
                fil_needed[filament.id] += needed
                fil_obj[filament.id] = filament
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
        Pasa el presupuesto a APPROVED y lo mete a producción: descuenta el stock
        de piezas, genera los trabajos de las que hay que imprimir, descuenta su
        material (filamento + agregados) y calcula la entrega estimada.

        El material se descuenta acá, al aprobar (no al imprimir), así el consumo
        impacta de inmediato en las métricas de inventario y costos.
        Devuelve {"from_stock": [...], "shortages": [...]}.
        Solo se puede aprobar en estado Borrador o Enviado.
        """
        approvable_statuses = {Presupuesto.Status.DRAFT, Presupuesto.Status.SENT}
        if self.status not in approvable_statuses:
            raise PresupuestoNotApprovableError(
                gettext(
                    "No se puede aprobar el presupuesto #%(pk)s: su estado es "
                    "'%(status)s'. Solo se pueden aprobar presupuestos en estado "
                    "Borrador o Enviado."
                )
                % {"pk": self.pk, "status": self.get_status_display()}
            )

        with transaction.atomic():
            self.status = Presupuesto.Status.APPROVED
            self.approved_at = timezone.now()
            self.save(update_fields=["status", "approved_at", "updated_at"])

            # Al aprobar, el pedido entra a producción: descuenta stock de piezas,
            # genera los trabajos, descuenta el material y arma la cola.
            result = self._provision_production()

        # Fuera de la transacción: recalcula cola y entrega.
        self.refresh_delivery()

        return result

    def apply_status_change(self, old_status):
        """
        Sincroniza las fechas de estado y dispara los efectos de producción
        cuando el estado se cambia "a mano" desde el admin (el dropdown del
        formulario, que no pasa por approve()).

        - Setea la fecha del nuevo estado si todavía está vacía (idempotente).
        - Al pasar a APROBADO, descuenta el inventario y genera la cola de
          producción (idempotente) y recalcula la entrega estimada.
        Devuelve {"from_stock": [...], "shortages": [...]} si hubo aprobación,
        si no None.

        Pensado para llamarse DESPUÉS de guardar los ítems (en save_related),
        así _provision_production() ve los productos del presupuesto.
        """
        Status = Presupuesto.Status
        if old_status == self.status:
            return None

        now = timezone.now()
        result = None

        if self.status == Status.SENT and not self.sent_at:
            self.sent_at = now
        elif self.status == Status.APPROVED:
            if not self.approved_at:
                self.approved_at = now
            result = self._provision_production()
        elif self.status == Status.IN_PRODUCTION and not self.production_started_at:
            self.production_started_at = now
        elif self.status == Status.COMPLETED:
            if not self.production_finished_at:
                self.production_finished_at = now
            if not self.completed_at:
                self.completed_at = now
            # Pedido para stock: al completarlo, sus terminados van al stock.
            self.add_finished_to_stock()
        elif self.status == Status.CANCELLED:
            # Cancelar un pedido aprobado revierte el inventario y cancela su cola.
            result = self.cancel(old_status=old_status)

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

        return result

    def cancel(self, old_status=None):
        """
        Cancela el pedido y, si estaba APROBADO o EN PRODUCCIÓN y todavía no se
        revirtió, devuelve el inventario que se había descontado al aprobar:

          - Trabajos en cola (no impresos): se cancelan y se DEVUELVE su filamento
            (no se van a imprimir, el material sigue en el estante).
          - Trabajos ya IMPRESOS: las piezas físicas pasan al stock de piezas. El
            filamento de esos ya se usó de verdad, así que no se devuelve.
          - Piezas que se habían tomado del stock de piezas: se devuelven.
          - Agregados: se devuelven completos (se usan recién al armar el pedido,
            y un pedido cancelado nunca llegó a armarse).

        No revierte si el pedido ya estaba COMPLETADO (ahí ya se entregó).
        Idempotente vía `stock_reversed`. Devuelve un resumen
        {"filaments": [...], "aggregates": [...], "piezas": [...], "jobs_cancelled": n}.
        """
        from collections import defaultdict

        from production.models import HistorialImpresion, ProductionJob

        from inventory.models import Aggregate, Filament, StockMovement

        Status = Presupuesto.Status
        JobStatus = ProductionJob.Status
        summary = {
            "filaments": [],
            "aggregates": [],
            "piezas": [],
            "finished": [],
            "jobs_cancelled": 0,
        }

        prev = old_status if old_status is not None else self.status
        do_reverse = (
            self.stock_provisioned
            and not self.stock_reversed
            and prev in (Status.APPROVED, Status.IN_PRODUCTION)
        )

        with transaction.atomic():
            if do_reverse:
                jobs = list(
                    self.jobs.select_related("pieza", "producto").exclude(
                        status=JobStatus.CANCELLED
                    )
                )
                # Cuánto se había encolado de cada pieza (impreso o por imprimir).
                queued_by_pieza = defaultdict(int)
                for job in jobs:
                    if job.pieza_id:
                        queued_by_pieza[job.pieza_id] += job.quantity

                for job in jobs:
                    if job.status == JobStatus.DONE:
                        # Ya impreso: las piezas del pedido pasan a stock. El
                        # filamento ya se consumió de verdad (no se devuelve).
                        if job.pieza_id and job.quantity:
                            Pieza.objects.filter(pk=job.pieza_id).update(
                                stock_quantity=models.F("stock_quantity")
                                + job.quantity
                            )
                            summary["piezas"].append(
                                {
                                    "pieza": job.pieza.name,
                                    "units": job.quantity,
                                    "motivo": gettext("ya impresa"),
                                }
                            )
                        continue

                    # Trabajo en cola (no impreso): devolvemos su filamento.
                    if job.stock_consumed:
                        self._return_job_filament(
                            job, summary, Filament, StockMovement
                        )
                    job.status = JobStatus.CANCELLED
                    job.save(update_fields=["status"])
                    job.register_history(estado=HistorialImpresion.Estado.CANCELADO)
                    summary["jobs_cancelled"] += 1

                # Piezas que se habían tomado del stock (no impresas): devolverlas.
                # Se reconstruye sobre lo que se PRODUJO (cantidad − lo servido del
                # stock de terminados), que es lo que generó piezas y agregados.
                required_by_pieza = defaultdict(int)
                pieza_obj = {}
                for item in self.items.select_related("producto").all():
                    produced = item.quantity - item.from_finished_stock
                    if produced <= 0:
                        continue
                    for pieza in item.producto.piezas.all():
                        required_by_pieza[pieza.pk] += pieza.units_needed * produced
                        pieza_obj[pieza.pk] = pieza
                for pid, required in required_by_pieza.items():
                    from_stock = required - queued_by_pieza.get(pid, 0)
                    if from_stock > 0:
                        Pieza.objects.filter(pk=pid).update(
                            stock_quantity=models.F("stock_quantity") + from_stock
                        )
                        summary["piezas"].append(
                            {
                                "pieza": pieza_obj[pid].name,
                                "units": from_stock,
                                "motivo": gettext("estaba en stock"),
                            }
                        )

                # Agregados (a nivel producto): se devuelven por lo producido.
                for item in self.items.select_related("producto").all():
                    produced = item.quantity - item.from_finished_stock
                    if produced <= 0:
                        continue
                    producto = item.producto
                    for line in producto.aggregate_lines.select_related(
                        "aggregate"
                    ).all():
                        qty = producto.aggregate_qty_needed(line, produced)
                        if qty <= 0:
                            continue
                        agg = line.aggregate
                        Aggregate.objects.filter(pk=agg.pk).update(
                            stock_quantity=models.F("stock_quantity") + qty
                        )
                        StockMovement.objects.create(
                            aggregate=agg,
                            quantity=qty,
                            reason=StockMovement.Reason.BUDGET_CANCELLED,
                            related_presupuesto=self,
                            note=gettext(
                                "Cancelación pedido #%(pk)s: %(producto)s ×%(qty)s"
                            )
                            % {"pk": self.pk, "producto": producto, "qty": produced},
                        )
                        summary["aggregates"].append({"item": str(agg), "units": qty})

                # Stock de PRODUCTOS TERMINADOS que se había servido al aprobar:
                # se devuelve (esas unidades nunca se entregaron).
                for item in self.items.select_related("producto").all():
                    if item.from_finished_stock > 0:
                        Producto.objects.filter(pk=item.producto_id).update(
                            stock_quantity=models.F("stock_quantity")
                            + item.from_finished_stock
                        )
                        summary["finished"].append(
                            {
                                "producto": str(item.producto),
                                "units": item.from_finished_stock,
                            }
                        )
                        item.from_finished_stock = 0
                        item.save(update_fields=["from_finished_stock"])

                self.stock_reversed = True
            else:
                # Nada que revertir: solo cancelamos los trabajos que quedaron
                # en cola (no tocamos los ya impresos).
                for job in self.jobs.exclude(
                    status__in=[JobStatus.DONE, JobStatus.CANCELLED]
                ):
                    job.status = JobStatus.CANCELLED
                    job.save(update_fields=["status"])
                    job.register_history(estado=HistorialImpresion.Estado.CANCELADO)
                    summary["jobs_cancelled"] += 1

            self.status = Status.CANCELLED
            self.save(update_fields=["status", "stock_reversed", "updated_at"])

        return summary

    def _return_job_filament(self, job, summary, Filament, StockMovement):
        """Devuelve al stock el filamento que consumió un trabajo no impreso y
        registra el movimiento de reversa. Lo usa cancel()."""
        if job.pieza_id:
            waste = job.producto.waste_multiplier
            runs = job.gcode_runs
            for line in job.pieza.filament_lines.select_related("filament").all():
                grams = (
                    Decimal(str(line.grams_used)) * runs * waste
                ).quantize(Decimal("0.01"))
                Filament.objects.filter(pk=line.filament_id).update(
                    stock_grams=models.F("stock_grams") + grams
                )
                StockMovement.objects.create(
                    filament_id=line.filament_id,
                    quantity=grams,
                    reason=StockMovement.Reason.BUDGET_CANCELLED,
                    related_presupuesto=self,
                    note=gettext("Cancelación pedido #%(pk)s: %(name)s ×%(qty)s")
                    % {"pk": self.pk, "name": job.pieza.name, "qty": job.quantity},
                )
                summary["filaments"].append(
                    {"item": str(line.filament), "grams": grams}
                )
        else:
            producto = job.producto
            for fil, grams_per_product in producto.aggregated_filament():
                grams = producto.filament_grams_needed(grams_per_product, job.quantity)
                Filament.objects.filter(pk=fil.pk).update(
                    stock_grams=models.F("stock_grams") + grams
                )
                StockMovement.objects.create(
                    filament=fil,
                    quantity=grams,
                    reason=StockMovement.Reason.BUDGET_CANCELLED,
                    related_presupuesto=self,
                    note=gettext("Cancelación pedido #%(pk)s: %(producto)s ×%(qty)s")
                    % {"pk": self.pk, "producto": producto, "qty": job.quantity},
                )
                summary["filaments"].append({"item": str(fil), "grams": grams})

    def add_finished_to_stock(self):
        """
        Si es un pedido 'para stock' que llegó a COMPLETADO, suma cada producto
        terminado al stock de productos terminados (Producto.stock_quantity).
        Idempotente vía `finished_stock_added`. Devuelve la lista de lo sumado.
        """
        if (
            not self.para_stock
            or self.finished_stock_added
            or self.status != Presupuesto.Status.COMPLETED
        ):
            return []

        added = []
        with transaction.atomic():
            for item in self.items.select_related("producto").all():
                if item.quantity <= 0:
                    continue
                Producto.objects.filter(pk=item.producto_id).update(
                    stock_quantity=models.F("stock_quantity") + item.quantity
                )
                added.append(
                    {"producto": str(item.producto), "units": item.quantity}
                )
            self.finished_stock_added = True
            self.save(update_fields=["finished_stock_added", "updated_at"])
        return added

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
        # Si el pedido es para stock y quedó completado, sus terminados van al
        # stock de productos terminados.
        if target == Status.COMPLETED:
            self.add_finished_to_stock()
        return True


class Metricas(Presupuesto):
    """
    Proxy de Presupuesto para tener en el admin una página propia de 'Métricas'
    (panel de KPIs de ventas, producción e inventario). No crea tabla nueva: la
    vista se arma en el admin con budgets.metrics.
    """

    class Meta:
        proxy = True
        verbose_name = _("Métrica")
        verbose_name_plural = _("Métricas")


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
    quantity = models.PositiveIntegerField(_("Cantidad de piezas"), default=1)
    unit_price = models.DecimalField(
        _("Precio unitario (congelado)"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_(
            "Precio por pieza guardado al armar el presupuesto. Si se deja "
            "vacío, se toma el precio actual del producto. Queda fijo aunque "
            "después cambie el costeo del producto."
        ),
    )
    from_finished_stock = models.PositiveIntegerField(
        _("Servido del stock de terminados"),
        default=0,
        help_text=_(
            "Unidades de esta línea que se sirvieron del stock de productos "
            "terminados al aprobar (no se produjeron). Se usa para devolver el "
            "stock si el pedido se cancela."
        ),
    )

    class Meta:
        verbose_name = _("Producto del presupuesto")
        verbose_name_plural = _("Productos del presupuesto")

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
