"""
Fechas editables: al corregir a mano la fecha de un documento, lo que
depende de ella (movimientos de stock, borradores automáticos de caja) se
mueve con ella. Los movimientos de caja ya confirmados no se tocan.
"""

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from budgets.models import Presupuesto
from gastos.models import CategoriaGasto, Gasto, MedioPagoGasto
from inventory.models import Compra, CompraLine, Filament, StockMovement
from production.models import Herramienta, HerramientaCategoria

from .models import CuentaCaja, MovimientoCaja


def _aware(y, m, d, h=12):
    return timezone.make_aware(dt.datetime(y, m, d, h))


class FechasEditablesTests(TestCase):
    def setUp(self):
        self.cuenta = CuentaCaja.objects.create(
            nombre="Efectivo",
            saldo_inicial=Decimal("0"),
            fecha_saldo_inicial=dt.date(2026, 1, 1),
            medio_pago_default=Presupuesto.MedioPago.EFECTIVO,
        )
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
        )

    def _compra(self, fecha=None):
        compra = Compra.objects.create(supplier="Prov", confirmed_at=fecha)
        CompraLine.objects.create(
            compra=compra, filament=self.fil, quantity=Decimal("1000"),
            unit_price=Decimal("20000"),
        )
        compra.confirm()
        compra.refresh_from_db()
        return compra

    # ---- Compras ----

    def test_compra_con_fecha_cargada_la_respeta_al_confirmar(self):
        fecha = _aware(2026, 10, 8)
        compra = self._compra(fecha)
        self.assertEqual(compra.confirmed_at, fecha)
        mov = StockMovement.objects.get(related_compra=compra)
        self.assertEqual(mov.created_at, fecha)
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("1000"))

    def test_compra_sin_fecha_usa_ahora(self):
        compra = self._compra()
        self.assertIsNotNone(compra.confirmed_at)
        self.assertLess(timezone.now() - compra.confirmed_at, dt.timedelta(minutes=1))

    def test_editar_fecha_de_compra_mueve_stock_y_borrador_de_caja(self):
        compra = self._compra(_aware(2026, 10, 8))
        borrador = MovimientoCaja.crear_borrador_automatico(
            cuenta=self.cuenta, tipo=MovimientoCaja.Tipo.EGRESO,
            monto=Decimal("20000"), fecha=dt.date(2026, 10, 8),
            concepto="Compra", compra=compra,
        )
        nueva = _aware(2026, 11, 2)
        compra.confirmed_at = nueva
        compra.save()
        self.assertEqual(StockMovement.objects.get(related_compra=compra).created_at, nueva)
        borrador.refresh_from_db()
        self.assertEqual(borrador.fecha, dt.date(2026, 11, 2))
        # El stock no se vuelve a sumar por editar la fecha.
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("1000"))

    def test_movimiento_de_caja_confirmado_no_se_toca(self):
        compra = self._compra(_aware(2026, 10, 8))
        confirmado = MovimientoCaja.objects.create(
            cuenta=self.cuenta, tipo=MovimientoCaja.Tipo.EGRESO,
            monto=Decimal("20000"), fecha=dt.date(2026, 10, 8),
            concepto="Compra", compra=compra,
        )
        compra.confirmed_at = _aware(2026, 11, 2)
        compra.save()
        confirmado.refresh_from_db()
        self.assertEqual(confirmado.fecha, dt.date(2026, 10, 8))

    # ---- Gastos y herramientas ----

    def test_editar_fecha_de_gasto_mueve_borrador_de_caja(self):
        medio = MedioPagoGasto.objects.get_or_create(nombre="Efectivo")[0]
        gasto = Gasto.objects.create(
            categoria=CategoriaGasto.objects.get_or_create(nombre="IT")[0],
            concepto="Hosting", monto=Decimal("50"), medio_pago=medio,
            fecha=dt.date(2026, 10, 1),
        )
        mov = MovimientoCaja.objects.get(gasto=gasto, origen_automatico=True)
        self.assertEqual(mov.fecha, dt.date(2026, 10, 1))
        gasto.fecha = dt.date(2026, 11, 15)
        gasto.save()
        mov.refresh_from_db()
        self.assertEqual(mov.fecha, dt.date(2026, 11, 15))

    def test_editar_fecha_de_herramienta_mueve_borrador_de_caja(self):
        herramienta = Herramienta.objects.create(
            nombre="Termoformadora",
            categoria=HerramientaCategoria.objects.get_or_create(nombre="Maquinaria")[0],
            costo_adquisicion=Decimal("1000"),
            medio_pago=Presupuesto.MedioPago.EFECTIVO,
            fecha_compra=dt.date(2026, 10, 1),
        )
        mov = MovimientoCaja.objects.get(herramienta=herramienta, origen_automatico=True)
        herramienta.fecha_compra = dt.date(2026, 10, 20)
        herramienta.save()
        mov.refresh_from_db()
        self.assertEqual(mov.fecha, dt.date(2026, 10, 20))

    # ---- Presupuestos ----

    def test_editar_aprobacion_mueve_solo_movimientos_de_la_aprobacion(self):
        aprobado = _aware(2026, 11, 20)
        pres = Presupuesto.objects.create(
            client_name="Cliente", status=Presupuesto.Status.APPROVED, approved_at=aprobado,
        )
        de_aprobacion = StockMovement.objects.create(
            filament=self.fil, quantity=Decimal("-10"),
            reason=StockMovement.Reason.PRODUCTION, related_presupuesto=pres,
            created_at=aprobado + dt.timedelta(seconds=2),
        )
        posterior = StockMovement.objects.create(
            filament=self.fil, quantity=Decimal("-5"),
            reason=StockMovement.Reason.PRODUCTION, related_presupuesto=pres,
            created_at=aprobado + dt.timedelta(days=3),
        )
        nueva = _aware(2026, 10, 5)
        pres.approved_at = nueva
        pres.save()
        de_aprobacion.refresh_from_db()
        posterior.refresh_from_db()
        self.assertEqual(de_aprobacion.created_at, nueva)
        self.assertEqual(posterior.created_at, aprobado + dt.timedelta(days=3))

    def test_editar_fecha_de_cobro_mueve_borrador_de_caja(self):
        pres = Presupuesto.objects.create(
            client_name="Cliente",
            fixed_cost=Decimal("1000"),
            estado_venta=Presupuesto.EstadoVenta.PAGADO,
            medio_pago=Presupuesto.MedioPago.EFECTIVO,
            fecha_cobro=dt.date(2026, 10, 1),
        )
        mov = MovimientoCaja.objects.get(presupuesto=pres, origen_automatico=True)
        pres.fecha_cobro = dt.date(2026, 10, 9)
        pres.save()
        mov.refresh_from_db()
        self.assertEqual(mov.fecha, dt.date(2026, 10, 9))

    def test_fecha_de_alta_del_presupuesto_es_editable(self):
        pres = Presupuesto.objects.create(client_name="Cliente")
        pres.created_at = _aware(2026, 9, 1)
        pres.save()
        pres.refresh_from_db()
        self.assertEqual(pres.created_at, _aware(2026, 9, 1))
