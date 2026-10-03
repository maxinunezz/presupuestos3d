import unicodedata
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

# Autogeneración de MovimientoCaja (app `caja`) al guardar un Gasto pagado
# con un medio de "caja inmediata". `Gasto.medio_pago` es un FK a
# `MedioPagoGasto` (nombre libre, lo da de alta el usuario), no el mismo
# TextChoices fijo que usa `Presupuesto.MedioPago` — así que mapeamos por
# nombre normalizado (sin acentos, minúsculas) al código de
# `Presupuesto.MedioPago` que usa `CuentaCaja.medio_pago_default`. Nombres
# que no figuren acá (Tarjeta, cuotas, Otro...) nunca autogeneran nada.
_NOMBRE_A_CODIGO_MEDIO_PAGO = {
    "efectivo": "EFECTIVO",
    "transferencia": "TRANSFERENCIA",
    "mercado pago": "MERCADOPAGO",
    "mercadopago": "MERCADOPAGO",
    "uala": "UALA",
}


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = texto.encode("ascii", "ignore").decode("ascii")
    return texto.strip().lower()


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


class AreaResponsable(models.Model):
    """
    Área del negocio responsable del gasto (ej: Producción, Ventas, Dirección).
    Es una clasificación aparte e independiente de `categoria`: la categoría
    dice "qué tipo de gasto es" (Administración, IT, etc.) y el área dice
    "quién lo generó / a quién se le imputa". Editable desde el admin para
    poder sumar áreas nuevas sin programar ni migrar nada. Es opcional: no
    todos los gastos necesitan tener un área asignada. Borrar un área en uso
    está bloqueado (`on_delete=PROTECT` en `Gasto`) para no perder el
    historial de gastos ya cargados con ella.
    """

    nombre = models.CharField(_("Nombre"), max_length=50, unique=True)

    class Meta:
        verbose_name = _("Área responsable")
        verbose_name_plural = _("Áreas responsables")
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class MedioPagoGasto(models.Model):
    """
    Medio de pago de un gasto (Efectivo, Transferencia, Tarjeta, etc.). Antes
    era un choice fijo en el código (`Gasto.MedioPago`); ahora es un modelo
    editable desde el admin para poder sumar medios nuevos (ej: "Ualá") sin
    programar ni migrar nada. Es opcional: no todos los gastos necesitan
    tener uno cargado. Borrar un medio en uso está bloqueado
    (`on_delete=PROTECT` en `Gasto`) para no perder el historial de gastos ya
    cargados con él.
    """

    nombre = models.CharField(_("Nombre"), max_length=50, unique=True)

    class Meta:
        verbose_name = _("Medio de pago")
        verbose_name_plural = _("Medios de pago")
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

    categoria = models.ForeignKey(
        CategoriaGasto,
        verbose_name=_("Categoría"),
        on_delete=models.PROTECT,
        related_name="gastos",
        default=_default_categoria_gasto,
    )
    area = models.ForeignKey(
        AreaResponsable,
        verbose_name=_("Área responsable"),
        on_delete=models.PROTECT,
        related_name="gastos",
        null=True,
        blank=True,
        help_text=_("Área del negocio responsable del gasto (opcional)."),
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
    medio_pago = models.ForeignKey(
        MedioPagoGasto,
        verbose_name=_("Medio de pago"),
        on_delete=models.PROTECT,
        related_name="gastos",
        null=True,
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
    cuota_actual = models.PositiveSmallIntegerField(
        _("Cuota actual"),
        null=True,
        blank=True,
        help_text=_(
            "Si este gasto es una cuota de un plan de pagos (ej: una impresora "
            "en 12 cuotas), qué número de cuota es este pago puntual (ej: 4 si "
            "es la cuota 4 de 12). Se usa junto con \"Cuotas totales\"."
        ),
    )
    cuotas_totales = models.PositiveSmallIntegerField(
        _("Cuotas totales"),
        null=True,
        blank=True,
        help_text=_(
            "Cuántas cuotas tiene el plan de pagos en total. Completalo junto "
            "con \"Cuota actual\" para que la proyección anual del compromiso "
            "mensual no siga contando este gasto después de terminar de "
            "pagarse. Dejalo vacío si no aplica (gasto recurrente indefinido)."
        ),
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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._autogenerar_movimiento_caja()

    def _autogenerar_movimiento_caja(self):
        """
        Si el medio de pago de este gasto es de "caja inmediata" (Efectivo,
        Transferencia, Mercado Pago o Ualá — tarjeta y cuotas SIEMPRE se
        cargan a mano) y hay una única `CuentaCaja` configurada para ese
        medio, crea un borrador de `MovimientoCaja` (EGRESO) para que el
        dueño lo revise y confirme desde el Panel de caja. Import diferido
        para evitar un ciclo (`caja` depende de `gastos`, no al revés).
        Idempotente: no duplica el borrador automático de un mismo gasto.
        """
        if not self.medio_pago_id:
            return
        codigo = _NOMBRE_A_CODIGO_MEDIO_PAGO.get(_normalizar(self.medio_pago.nombre))
        if not codigo:
            return

        from caja.models import CuentaCaja, MovimientoCaja

        if MovimientoCaja.objects.filter(gasto=self, origen_automatico=True).exists():
            return

        cuentas = CuentaCaja.objects.filter(medio_pago_default=codigo, is_active=True)
        if cuentas.count() != 1:
            return

        MovimientoCaja.crear_borrador_automatico(
            cuenta=cuentas.first(),
            tipo=MovimientoCaja.Tipo.EGRESO,
            monto=self.monto,
            fecha=self.fecha,
            concepto=gettext("Gasto #%(pk)s — %(concepto)s")
            % {"pk": self.pk, "concepto": self.concepto},
            gasto=self,
        )

    def clean(self):
        super().clean()
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

    @property
    def meses_restantes(self) -> int | None:
        """
        Cuotas que faltan pagar DESPUÉS de esta (para no proyectar el
        compromiso mensual más allá de cuando termina el plan de pagos).
        None = indefinido / no aplica (no es un gasto en cuotas).
        """
        if not (self.cuotas_totales and self.cuota_actual):
            return None
        return max(self.cuotas_totales - self.cuota_actual, 0)

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
