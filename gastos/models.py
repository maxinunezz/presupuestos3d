from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class CategoriaGasto(models.Model):
    """
    Categoría de gasto (Administración, Comercialización, Suscripciones, IT,
    Herramientas, etc.). Antes era un choice fijo en el código (`Gasto.Categoria`);
    ahora es un modelo editable desde el admin para poder sumar categorías
    nuevas sin programar ni migrar nada — el dueño del negocio las da de alta
    él mismo en "Categorías de gasto". Borrar una categoría en uso está
    bloqueado (`on_delete=PROTECT` en `Gasto`/`TopeGasto`) para no perder el
    historial de gastos ya cargados con ella.
    """

    nombre = models.CharField(_("Nombre"), max_length=50, unique=True)

    class Meta:
        verbose_name = _("Categoría de gasto")
        verbose_name_plural = _("Categorías de gasto")
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


def _default_categoria_gasto():
    """
    Categoría que se preselecciona al cargar un Gasto nuevo: "Administración"
    si existe (la que era default antes de este modelo), si no la primera que
    haya. Devuelve None si todavía no hay ninguna categoría cargada.
    """
    return (
        CategoriaGasto.objects.filter(nombre="Administración")
        .values_list("id", flat=True)
        .first()
        or CategoriaGasto.objects.order_by("id").values_list("id", flat=True).first()
    )


class Gasto(models.Model):
    """
    Un gasto operativo / de estructura del negocio: NO es un costo directo de
    producción (filamento, máquina, mano de obra) sino plata que sale por la
    operación general (administración, comercialización, suscripciones, IT).

    Se carga un Gasto por cada erogación real, con su fecha. Esa fecha define en
    qué mes/año cae en el panel. Los gastos recurrentes (suscripciones, IT) se
    marcan con `es_recurrente` + `periodicidad` para calcular el compromiso
    mensual (run-rate).

    `tipo` distingue OPERATIVO (estructura del negocio, entra en el resultado
    operativo vs ventas) de EXTRAORDINARIO (puntual y no representativo: viajes,
    imprevistos; se muestra aparte, informativo, para no distorsionar el
    resultado). Es independiente de `es_recurrente`: un gasto operativo puede
    ser único (ej: honorarios de un trámite) y no por eso es extraordinario.
    """

    class Tipo(models.TextChoices):
        OPERATIVO = "OPERATIVO", _("Operativo (estructura del negocio)")
        EXTRAORDINARIO = "EXTRAORDINARIO", _("Extraordinario (puntual, no representativo)")

    class Periodicidad(models.TextChoices):
        UNICA = "UNICA", _("Único (no se repite)")
        MENSUAL = "MENSUAL", _("Mensual")
        ANUAL = "ANUAL", _("Anual")

    class MedioPago(models.TextChoices):
        EFECTIVO = "EFECTIVO", _("Efectivo")
        TRANSFERENCIA = "TRANSFERENCIA", _("Transferencia")
        TARJETA = "TARJETA", _("Tarjeta de crédito")
        DEBITO = "DEBITO", _("Débito automático")
        OTRO = "OTRO", _("Otro")

    categoria = models.ForeignKey(
        CategoriaGasto,
        verbose_name=_("Categoría"),
        on_delete=models.PROTECT,
        related_name="gastos",
        default=_default_categoria_gasto,
    )
    tipo = models.CharField(
        _("Tipo"),
        max_length=20,
        choices=Tipo.choices,
        default=Tipo.OPERATIVO,
        help_text=_(
            "Operativo: gasto de estructura del negocio (entra en el resultado "
            "operativo vs ventas). Extraordinario: gasto puntual y no "
            "representativo (viajes, imprevistos) que se muestra aparte para no "
            "distorsionar el resultado."
        ),
    )
    concepto = models.CharField(
        _("Concepto"),
        max_length=150,
        help_text=_("Qué gasto es (ej: Contador, Google Workspace, Publicidad Instagram)."),
    )
    monto = models.DecimalField(_("Monto ($)"), max_digits=12, decimal_places=2)
    fecha = models.DateField(
        _("Fecha"),
        default=timezone.localdate,
        help_text=_("Fecha del gasto. Define en qué mes/año cae en el panel."),
    )
    proveedor = models.CharField(_("Proveedor"), max_length=150, blank=True)
    medio_pago = models.CharField(
        _("Medio de pago"),
        max_length=20,
        choices=MedioPago.choices,
        blank=True,
    )
    es_recurrente = models.BooleanField(
        _("Es recurrente"),
        default=False,
        help_text=_(
            "Marcalo si es un gasto fijo que se repite (suscripción, abono). "
            "Se usa para calcular el compromiso mensual (run-rate)."
        ),
    )
    periodicidad = models.CharField(
        _("Periodicidad"),
        max_length=10,
        choices=Periodicidad.choices,
        default=Periodicidad.UNICA,
        help_text=_("Si es recurrente, cada cuánto se paga (para el compromiso mensual)."),
    )
    notas = models.TextField(_("Notas"), blank=True)
    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado"), auto_now=True)

    class Meta:
        verbose_name = _("Gasto")
        verbose_name_plural = _("Gastos")
        ordering = ["-fecha", "-id"]

    def __str__(self):
        return f"{self.categoria} · {self.concepto} (${self.monto})"

    @property
    def monthly_equivalent(self) -> Decimal:
        """Equivalente mensual del gasto recurrente (para el run-rate)."""
        if not self.es_recurrente:
            return Decimal("0")
        monto = Decimal(self.monto or 0)
        if self.periodicidad == self.Periodicidad.ANUAL:
            return (monto / Decimal("12")).quantize(Decimal("0.01"))
        # Mensual (o recurrente sin periodicidad clara): cuenta el monto completo.
        return monto.quantize(Decimal("0.01"))


class TopeGasto(models.Model):
    """
    Tope (presupuesto) mensual por categoría de gasto. En el panel se compara el
    gasto real del período contra este tope y se avisa si se excede.
    """

    categoria = models.ForeignKey(
        CategoriaGasto,
        verbose_name=_("Categoría"),
        on_delete=models.PROTECT,
        related_name="topes",
        unique=True,
    )
    monto_mensual = models.DecimalField(
        _("Tope mensual ($)"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_("Gasto máximo esperado por mes para esta categoría. 0 = sin tope."),
    )
    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado"), auto_now=True)

    class Meta:
        verbose_name = _("Tope de gasto (presupuesto)")
        verbose_name_plural = _("Topes de gasto (presupuestos)")
        ordering = ["categoria__nombre"]

    def __str__(self):
        from django.utils.translation import gettext
        return gettext("Tope %(categoria)s: $%(monto)s/mes") % {
            "categoria": self.categoria,
            "monto": self.monto_mensual,
        }


class PanelGastos(Gasto):
    """Proxy para tener en el admin la página 'Panel de gastos' (solo lectura)."""

    class Meta:
        proxy = True
        verbose_name = _("Panel de gastos")
        verbose_name_plural = _("Panel de gastos")
