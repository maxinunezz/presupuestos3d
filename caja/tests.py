from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import (
    Pieza,
    PiezaFilamentLine,
    Presupuesto,
    PresupuestoItem,
    Producto,
)
from gastos.models import CategoriaGasto, Gasto, MedioPagoGasto
from inventory.models import Filament
from production.models import Herramienta, HerramientaCategoria

from .models import CuentaCaja, MovimientoCaja


def make_producto(**kwargs):
    defaults = dict(
        name="Pieza",
        machine_cost_per_hour=Decimal("100"),
        waste_percent=Decimal("0"),
        labor_cost_per_hour=Decimal("0"),
        sale_price=Decimal("0"),
        diseno_listo=True,
    )
    defaults.update(kwargs)
    return Producto.objects.create(**defaults)


def add_pieza(producto, filament, grams, print_hours=Decimal("2"), name="Pieza principal"):
    pieza = Pieza.objects.create(
        producto=producto,
        name=name,
        units_needed=1,
        pieces_per_gcode=1,
        print_time_minutes=print_hours * 60,
    )
    PiezaFilamentLine.objects.create(pieza=pieza, filament=filament, grams_used=grams)
    return pieza


class CuentaCajaSaldoActualTests(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("1000"),
            fecha_saldo_inicial=self.hoy - timedelta(days=30),
        )

    def _mov(self, tipo, monto, estado, fecha=None):
        return MovimientoCaja.objects.create(
            cuenta=self.cuenta,
            tipo=tipo,
            monto=monto,
            fecha=fecha or self.hoy,
            concepto="x",
            estado=estado,
        )

    def test_suma_confirmados_e_ignora_borrador_y_descartado(self):
        self._mov(MovimientoCaja.Tipo.INGRESO, Decimal("500"), MovimientoCaja.Estado.CONFIRMADO)
        self._mov(MovimientoCaja.Tipo.EGRESO, Decimal("200"), MovimientoCaja.Estado.CONFIRMADO)
        self._mov(MovimientoCaja.Tipo.INGRESO, Decimal("9999"), MovimientoCaja.Estado.BORRADOR)
        self._mov(MovimientoCaja.Tipo.INGRESO, Decimal("9999"), MovimientoCaja.Estado.DESCARTADO)
        self.assertEqual(self.cuenta.saldo_actual, Decimal("1300"))

    def test_ignora_movimientos_anteriores_a_fecha_saldo_inicial(self):
        self._mov(
            MovimientoCaja.Tipo.INGRESO,
            Decimal("5000"),
            MovimientoCaja.Estado.CONFIRMADO,
            fecha=self.hoy - timedelta(days=60),
        )
        self.assertEqual(self.cuenta.saldo_actual, Decimal("1000"))


class PresupuestoAutogeneraMovimientoCajaTests(TestCase):
    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("1000000"),
        )
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("0"),
            fecha_saldo_inicial=timezone.localdate() - timedelta(days=1),
            medio_pago_default=Presupuesto.MedioPago.EFECTIVO,
        )

    def _presupuesto(self):
        p = make_producto(sale_price=Decimal("1000"))
        add_pieza(p, self.fil, Decimal("10"), print_hours=Decimal("1"))
        pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(presupuesto=pres, producto=p, quantity=1)
        pres.approve()
        return pres

    def test_crea_borrador_con_medio_de_caja_inmediata(self):
        pres = self._presupuesto()
        pres.estado_venta = Presupuesto.EstadoVenta.PAGADO
        pres.medio_pago = Presupuesto.MedioPago.EFECTIVO
        pres.save()

        movs = MovimientoCaja.objects.filter(presupuesto=pres, origen_automatico=True)
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.estado, MovimientoCaja.Estado.BORRADOR)
        self.assertEqual(mov.tipo, MovimientoCaja.Tipo.INGRESO)
        self.assertEqual(mov.monto, pres.total)
        self.assertEqual(mov.cuenta, self.cuenta)

        # Idempotente: guardar de nuevo no duplica el movimiento.
        pres.save()
        self.assertEqual(
            MovimientoCaja.objects.filter(presupuesto=pres, origen_automatico=True).count(),
            1,
        )

    def test_no_crea_nada_con_tarjeta(self):
        pres = self._presupuesto()
        pres.estado_venta = Presupuesto.EstadoVenta.PAGADO
        pres.medio_pago = Presupuesto.MedioPago.TARJETA
        pres.save()
        self.assertFalse(
            MovimientoCaja.objects.filter(presupuesto=pres, origen_automatico=True).exists()
        )

    def test_no_crea_nada_sin_cuenta_configurada(self):
        self.cuenta.medio_pago_default = ""
        self.cuenta.save()
        pres = self._presupuesto()
        pres.estado_venta = Presupuesto.EstadoVenta.PAGADO
        pres.medio_pago = Presupuesto.MedioPago.EFECTIVO
        pres.save()
        self.assertFalse(
            MovimientoCaja.objects.filter(presupuesto=pres, origen_automatico=True).exists()
        )


class PresupuestoCancelRevierteMovimientoCajaTests(TestCase):
    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("1000000"),
        )
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("0"),
            fecha_saldo_inicial=timezone.localdate() - timedelta(days=1),
        )

    def _presupuesto(self):
        p = make_producto(sale_price=Decimal("1000"))
        add_pieza(p, self.fil, Decimal("10"), print_hours=Decimal("1"))
        pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(presupuesto=pres, producto=p, quantity=1)
        pres.approve()
        return pres

    def test_confirmado_genera_contra_asiento_en_borrador(self):
        pres = self._presupuesto()
        mov = MovimientoCaja.objects.create(
            cuenta=self.cuenta,
            tipo=MovimientoCaja.Tipo.INGRESO,
            monto=Decimal("1000"),
            fecha=timezone.localdate(),
            concepto="Cobro manual",
            estado=MovimientoCaja.Estado.CONFIRMADO,
            presupuesto=pres,
        )

        pres.cancel()

        contra = MovimientoCaja.objects.filter(
            presupuesto=pres, origen_automatico=True
        ).exclude(pk=mov.pk)
        self.assertEqual(contra.count(), 1)
        contra_mov = contra.first()
        self.assertEqual(contra_mov.tipo, MovimientoCaja.Tipo.EGRESO)
        self.assertEqual(contra_mov.monto, Decimal("1000"))
        self.assertEqual(contra_mov.cuenta, self.cuenta)
        self.assertEqual(contra_mov.estado, MovimientoCaja.Estado.BORRADOR)

        mov.refresh_from_db()
        self.assertEqual(mov.estado, MovimientoCaja.Estado.CONFIRMADO)

    def test_borrador_se_descarta(self):
        pres = self._presupuesto()
        mov = MovimientoCaja.objects.create(
            cuenta=self.cuenta,
            tipo=MovimientoCaja.Tipo.INGRESO,
            monto=Decimal("1000"),
            fecha=timezone.localdate(),
            concepto="Borrador automático",
            estado=MovimientoCaja.Estado.BORRADOR,
            origen_automatico=True,
            presupuesto=pres,
        )

        pres.cancel()

        mov.refresh_from_db()
        self.assertEqual(mov.estado, MovimientoCaja.Estado.DESCARTADO)
        # No se crea ningún contra-asiento nuevo para un borrador descartado.
        self.assertEqual(
            MovimientoCaja.objects.filter(presupuesto=pres).count(), 1
        )


class MovimientoCajaAdminActionsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("0"),
            fecha_saldo_inicial=timezone.localdate(),
        )

    def _mov(self, estado):
        return MovimientoCaja.objects.create(
            cuenta=self.cuenta,
            tipo=MovimientoCaja.Tipo.INGRESO,
            monto=Decimal("100"),
            fecha=timezone.localdate(),
            concepto="x",
            estado=estado,
        )

    def test_confirmar_seleccionados(self):
        mov = self._mov(MovimientoCaja.Estado.BORRADOR)
        resp = self.client.post(
            reverse("admin:caja_movimientocaja_changelist"),
            {
                "action": "confirmar_seleccionados",
                "_selected_action": [str(mov.pk)],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        mov.refresh_from_db()
        self.assertEqual(mov.estado, MovimientoCaja.Estado.CONFIRMADO)

    def test_descartar_seleccionados(self):
        mov = self._mov(MovimientoCaja.Estado.BORRADOR)
        resp = self.client.post(
            reverse("admin:caja_movimientocaja_changelist"),
            {
                "action": "descartar_seleccionados",
                "_selected_action": [str(mov.pk)],
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        mov.refresh_from_db()
        self.assertEqual(mov.estado, MovimientoCaja.Estado.DESCARTADO)


class PanelCajaViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        self.url = reverse("admin:caja_panelcaja_changelist")
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("500"),
            fecha_saldo_inicial=timezone.localdate() - timedelta(days=5),
        )
        MovimientoCaja.objects.create(
            cuenta=self.cuenta,
            tipo=MovimientoCaja.Tipo.INGRESO,
            monto=Decimal("1000"),
            fecha=timezone.localdate(),
            concepto="Cobro",
            estado=MovimientoCaja.Estado.CONFIRMADO,
        )

    def test_pagina_responde_200(self):
        resp = self.client.get(self.url, {"period": "month"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ingresos reales del período")

    def test_export_xlsx(self):
        resp = self.client.get(self.url, {"period": "month", "export": "xlsx"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertTrue(len(resp.content) > 0)


class GastoAutogeneraMovimientoCajaTests(TestCase):
    """`Gasto.medio_pago` es un FK a `MedioPagoGasto` (nombre libre); se
    mapea por nombre normalizado al código de `Presupuesto.MedioPago` que
    usa `CuentaCaja.medio_pago_default`."""

    def setUp(self):
        self.categoria = CategoriaGasto.objects.get_or_create(nombre="IT")[0]
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("0"),
            fecha_saldo_inicial=timezone.localdate() - timedelta(days=1),
            medio_pago_default=Presupuesto.MedioPago.EFECTIVO,
        )

    def test_crea_egreso_borrador_con_medio_de_caja_inmediata(self):
        medio = MedioPagoGasto.objects.get_or_create(nombre="Efectivo")[0]
        gasto = Gasto.objects.create(
            categoria=self.categoria,
            concepto="Hosting",
            monto=Decimal("50"),
            medio_pago=medio,
        )
        movs = MovimientoCaja.objects.filter(gasto=gasto, origen_automatico=True)
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.tipo, MovimientoCaja.Tipo.EGRESO)
        self.assertEqual(mov.estado, MovimientoCaja.Estado.BORRADOR)
        self.assertEqual(mov.monto, Decimal("50"))
        self.assertEqual(mov.cuenta, self.cuenta)

        # Idempotente.
        gasto.save()
        self.assertEqual(
            MovimientoCaja.objects.filter(gasto=gasto, origen_automatico=True).count(), 1
        )

    def test_no_crea_nada_con_tarjeta(self):
        medio = MedioPagoGasto.objects.create(nombre="Tarjeta")
        gasto = Gasto.objects.create(
            categoria=self.categoria,
            concepto="Compra insumo",
            monto=Decimal("50"),
            medio_pago=medio,
        )
        self.assertFalse(
            MovimientoCaja.objects.filter(gasto=gasto, origen_automatico=True).exists()
        )

    def test_no_crea_nada_sin_medio_de_pago(self):
        gasto = Gasto.objects.create(
            categoria=self.categoria, concepto="Sin medio", monto=Decimal("50")
        )
        self.assertFalse(
            MovimientoCaja.objects.filter(gasto=gasto, origen_automatico=True).exists()
        )


class HerramientaAutogeneraMovimientoCajaTests(TestCase):
    """`Herramienta.medio_pago` usa directamente los códigos de
    `Presupuesto.MedioPago` (mismo mecanismo que `Presupuesto`), a
    diferencia de `Gasto` que mapea por nombre desde un FK libre."""

    def setUp(self):
        self.categoria = HerramientaCategoria.objects.get_or_create(nombre="Maquinaria")[0]
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("0"),
            fecha_saldo_inicial=timezone.localdate() - timedelta(days=1),
            medio_pago_default=Presupuesto.MedioPago.EFECTIVO,
        )

    def test_crea_egreso_borrador_con_medio_de_caja_inmediata(self):
        herramienta = Herramienta.objects.create(
            nombre="Termoformadora",
            categoria=self.categoria,
            costo_adquisicion=Decimal("1000"),
            medio_pago=Presupuesto.MedioPago.EFECTIVO,
        )
        movs = MovimientoCaja.objects.filter(herramienta=herramienta, origen_automatico=True)
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.tipo, MovimientoCaja.Tipo.EGRESO)
        self.assertEqual(mov.estado, MovimientoCaja.Estado.BORRADOR)
        self.assertEqual(mov.monto, Decimal("1000"))
        self.assertEqual(mov.cuenta, self.cuenta)

        # Idempotente.
        herramienta.save()
        self.assertEqual(
            MovimientoCaja.objects.filter(
                herramienta=herramienta, origen_automatico=True
            ).count(),
            1,
        )

    def test_no_crea_nada_con_tarjeta(self):
        herramienta = Herramienta.objects.create(
            nombre="Sacabocados",
            categoria=self.categoria,
            costo_adquisicion=Decimal("50"),
            medio_pago=Presupuesto.MedioPago.TARJETA,
        )
        self.assertFalse(
            MovimientoCaja.objects.filter(
                herramienta=herramienta, origen_automatico=True
            ).exists()
        )

    def test_no_crea_nada_sin_medio_de_pago(self):
        herramienta = Herramienta.objects.create(
            nombre="Sin medio",
            categoria=self.categoria,
            costo_adquisicion=Decimal("50"),
        )
        self.assertFalse(
            MovimientoCaja.objects.filter(
                herramienta=herramienta, origen_automatico=True
            ).exists()
        )
