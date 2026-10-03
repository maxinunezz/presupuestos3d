"""
Libro diario de caja: flujo de fondos REAL del negocio (a diferencia del
devengado que mide `budgets.metrics`).

Reglas de negocio (ya acordadas, no renegociar):
  - Solo los medios de pago de "caja inmediata" (Efectivo, Transferencia,
    Mercado Pago, Ualá) disparan la autogeneración de un BORRADOR de
    `MovimientoCaja`. Tarjeta (débito/crédito) y cualquier cuota SIEMPRE se
    cargan a mano, porque la plata no entra/sale ese mismo día.
  - Puede haber varias cuentas de caja (Efectivo, Banco, Mercado Pago...),
    cada una con su propio saldo.
  - Un solo medio de pago por documento (no hay pagos mixtos modelados): si
    en la realidad un cobro fue mixto, se cargan dos movimientos a mano.
  - Sin restricciones de borrado/edición sobre `MovimientoCaja`: CRUD libre,
    el dueño es el único usuario del sistema.

Todo el dinero vive en columnas acá (a diferencia de `Presupuesto`/`Producto`
en `budgets`, donde el dinero es @property): un movimiento de caja ES un
hecho con un monto fijo, no algo que se recalcula.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

ZERO = Decimal("0")


def _medio_pago_choices():
    """
    Reutiliza los choices de `Presupuesto.MedioPago` (budgets) para que
    `CuentaCaja.medio_pago_default` ofrezca las mismas opciones, sin
    duplicarlas a mano. `caja` depende de `budgets` (no al revés), así que
    este import a nivel de módulo es seguro.
    """
    from budgets.models import Presupuesto

    return Presupuesto.MedioPago.choices


class CuentaCaja(models.Model):
    """
    Una cuenta de caja real (Efectivo, Banco, Mercado Pago, Ualá...), cada
    una con su propio saldo. `saldo_inicial` es el monto que esa cuenta tenía
    de verdad al momento de arrancar a usar este libro diario (para no
    empezar en $0 artificial); `fecha_saldo_inicial` dice desde cuándo vale
    ese número, así los movimientos se suman solo desde ahí en adelante.
    """

    nombre = models.CharField(_("Nombre"), max_length=100, unique=True)
    saldo_inicial = models.DecimalField(
        _("Saldo inicial"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_(
            "Monto real que esta cuenta tenía al momento de arrancar a "
            "usarla en el sistema, para no empezar en $0 artificial."
        ),
    )
    fecha_saldo_inicial = models.DateField(
        _("Fecha del saldo inicial"),
        help_text=_(
            "Desde cuándo vale el saldo inicial. Los movimientos confirmados "
            "se suman desde esta fecha en adelante."
        ),
    )
    medio_pago_default = models.CharField(
        _("Medio de pago que recibe"),
        max_length=20,
        choices=_medio_pago_choices(),
        blank=True,
        default="",
        help_text=_(
            "Si esta cuenta recibe los cobros/pagos de un medio de pago "
            "puntual (ej: Efectivo, Mercado Pago), configuralo acá para que "
            "el sistema arme solo el borrador de caja al marcar un "
            "presupuesto como Pagado con ese medio. Dejalo vacío si esta "
            "cuenta no recibe autogeneración."
        ),
    )
    is_active = models.BooleanField(_("Activa"), default=True)
    order = models.PositiveIntegerField(_("Orden"), default=0)

    class Meta:
        verbose_name = _("Cuenta de caja")
        verbose_name_plural = _("Cuentas de caja")
        ordering = ["order", "nombre"]

    def __str__(self):
        return self.nombre

    @property
    def saldo_actual(self) -> Decimal:
        """
        `saldo_inicial` + movimientos CONFIRMADOS de esta cuenta con
        `fecha >= fecha_saldo_inicial` (ingresos suman, egresos restan).
        Se itera en Python, siguiendo la convención del proyecto de que el
        dinero se calcula en Python (ver `Producto`/`Presupuesto` en
        `budgets.models`).
        """
        total = self.saldo_inicial
        movimientos = self.movimientos.filter(
            estado=MovimientoCaja.Estado.CONFIRMADO,
            fecha__gte=self.fecha_saldo_inicial,
        )
        for mov in movimientos:
            if mov.tipo == MovimientoCaja.Tipo.INGRESO:
                total += mov.monto
            else:
                total -= mov.monto
        return total


class MovimientoCaja(models.Model):
    """
    Un movimiento real (o borrador) del libro diario de caja: una entrada o
    salida de plata de una `CuentaCaja`, en una fecha concreta.

    `estado`: los movimientos cargados a mano nacen CONFIRMADOS (afectan el
    saldo real ya mismo); los que genera el sistema automáticamente
    (`origen_automatico=True`) nacen en BORRADOR, para que el usuario los
    revise y confirme desde el Panel de caja.
    """

    class Tipo(models.TextChoices):
        INGRESO = "INGRESO", _("Ingreso")
        EGRESO = "EGRESO", _("Egreso")

    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", _("Borrador")
        CONFIRMADO = "CONFIRMADO", _("Confirmado")
        DESCARTADO = "DESCARTADO", _("Descartado")

    cuenta = models.ForeignKey(
        CuentaCaja,
        verbose_name=_("Cuenta"),
        on_delete=models.PROTECT,
        related_name="movimientos",
    )
    fecha = models.DateField(
        _("Fecha"),
        help_text=_(
            "Fecha real del movimiento de plata (no la fecha del documento "
            "de origen, si tiene uno)."
        ),
    )
    tipo = models.CharField(_("Tipo"), max_length=10, choices=Tipo.choices)
    monto = models.DecimalField(
        _("Monto"),
        max_digits=12,
        decimal_places=2,
        help_text=_("Siempre positivo; el signo lo da el tipo (Ingreso/Egreso)."),
    )
    concepto = models.CharField(_("Concepto"), max_length=200)
    estado = models.CharField(
        _("Estado"),
        max_length=10,
        choices=Estado.choices,
        default=Estado.CONFIRMADO,
        help_text=_(
            "Los movimientos cargados a mano nacen Confirmados. Los que "
            "genera el sistema automáticamente nacen en Borrador, para "
            "revisarlos y confirmarlos antes de que afecten el saldo real."
        ),
    )
    origen_automatico = models.BooleanField(
        _("Generado automáticamente"),
        default=False,
        help_text=_("Si lo generó el sistema (a diferencia de cargado a mano)."),
    )

    # Trazabilidad opcional hacia el documento de origen. Como máximo UNO de
    # estos tres puede estar seteado (validado en clean()). Referencias por
    # string: `caja` depende de `budgets`/`inventory`/`gastos`, no al revés.
    presupuesto = models.ForeignKey(
        "budgets.Presupuesto",
        verbose_name=_("Presupuesto"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_caja",
    )
    compra = models.ForeignKey(
        "inventory.Compra",
        verbose_name=_("Compra"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_caja",
    )
    gasto = models.ForeignKey(
        "gastos.Gasto",
        verbose_name=_("Gasto"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_caja",
    )

    created_at = models.DateTimeField(_("Creado"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado"), auto_now=True)

    class Meta:
        verbose_name = _("Movimiento de caja")
        verbose_name_plural = _("Movimientos de caja")
        ordering = ["-fecha", "-pk"]

    def __str__(self):
        signo = "+" if self.tipo == self.Tipo.INGRESO else "-"
        return f"{self.fecha} {signo}{self.monto} ({self.concepto})"

    def clean(self):
        super().clean()
        if self.monto is not None and self.monto <= 0:
            raise ValidationError(
                {"monto": _("El monto tiene que ser mayor a cero.")}
            )
        vinculos = [self.presupuesto_id, self.compra_id, self.gasto_id]
        if sum(1 for v in vinculos if v) > 1:
            raise ValidationError(
                _(
                    "Un movimiento de caja puede estar vinculado a lo sumo a "
                    "un solo documento de origen (Presupuesto, Compra o "
                    "Gasto)."
                )
            )

    @classmethod
    def crear_borrador_automatico(
        cls, *, cuenta, tipo, monto, fecha, concepto, **origen_kwargs
    ):
        """
        Crea (y guarda) un `MovimientoCaja` en estado Borrador generado por
        el sistema. `origen_kwargs` puede traer `presupuesto=`, `compra=` o
        `gasto=` (a lo sumo uno). Llama `full_clean()` antes de guardar para
        no saltear las validaciones de `clean()` al crear por código.
        """
        mov = cls(
            cuenta=cuenta,
            tipo=tipo,
            monto=monto,
            fecha=fecha,
            concepto=concepto,
            estado=cls.Estado.BORRADOR,
            origen_automatico=True,
            **origen_kwargs,
        )
        mov.full_clean()
        mov.save()
        return mov


class PanelCaja(MovimientoCaja):
    """
    Proxy de `MovimientoCaja` para tener en el admin una página propia de
    solo lectura, "Panel de caja": flujo de fondos REAL del período
    (ingresos y egresos CONFIRMADOS) + saldo actual por cuenta + cuántos
    movimientos quedaron pendientes de revisar. No crea tabla nueva.
    """

    class Meta:
        proxy = True
        verbose_name = _("Caja")
        verbose_name_plural = _("Panel de caja")
