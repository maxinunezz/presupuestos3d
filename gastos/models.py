from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Gasto(models.Model):
    """
    Un gasto operativo / de estructura del negocio: NO es un costo directo de
    producción (filamento, máquina, mano de obra) sino plata que sale por la
    operación general (administración, comercialización, suscripciones, IT).

    Se carga un Gasto por cada erogación real, con su fecha. Esa fecha define en
    qué mes/año cae en el panel. Los gastos recurrentes (suscripciones, IT) se
    marcan con `es_recurrente` + `periodicidad` para calcular el compromiso
    mensual (run-rate).
    """

    class Categoria(models.TextChoices):
        ADMIN = "ADMIN", _("Administración")
        COMMERCIAL = "COMMERCIAL", _("Comercialización")
        SUBSCRIPTION = "SUBSCRIPTION", _("Suscripciones")
        IT = "IT", _("IT")
        OTHER = "OTHER", _("Otro")

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

    categoria = models.CharField(
        _("Categoría"), max_length=20, choices=Categoria.choices, default=Categoria.ADMIN
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
        return f"{self.get_categoria_display()} · {self.concepto} (${self.monto})"

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

    categoria = models.CharField(
        _("Categoría"),
        max_length=20,
        choices=Gasto.Categoria.choices,
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
        ordering = ["categoria"]

    def __str__(self):
        from django.utils.translation import gettext
        return gettext("Tope %(categoria)s: $%(monto)s/mes") % {
            "categoria": self.get_categoria_display(),
            "monto": self.monto_mensual,
        }


class PanelGastos(Gasto):
    """Proxy para tener en el admin la página 'Panel de gastos' (solo lectura)."""

    class Meta:
        proxy = True
        verbose_name = _("Panel de gastos")
        verbose_name_plural = _("Panel de gastos")
