from decimal import Decimal

from django.test import TestCase

from inventory.models import Filament
from production.models import Maquina

from .models import (
    Presupuesto,
    PresupuestoItem,
    Producto,
    ProductoFilamentLine,
)


def make_producto(**kwargs):
    defaults = dict(
        name="Pieza",
        print_time_hours=Decimal("2"),
        machine_cost_per_hour=Decimal("100"),
        labor_cost_per_hour=Decimal("0"),
        margin_percent=Decimal("0"),
    )
    defaults.update(kwargs)
    return Producto.objects.create(**defaults)


class ProductoCosteoTests(TestCase):
    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),  # $10/g
            stock_grams=Decimal("10000"),
        )

    def test_unit_cost_y_unit_price(self):
        p = make_producto(margin_percent=Decimal("50"))
        ProductoFilamentLine.objects.create(
            producto=p, filament=self.fil, grams_used=Decimal("100")
        )
        # material 100g * $10 = 1000 ; máquina 2h * 100 = 200 ; total 1200
        self.assertEqual(p.material_cost, Decimal("1000.00"))
        self.assertEqual(p.machine_cost, Decimal("200.00"))
        self.assertEqual(p.unit_cost, Decimal("1200.00"))
        # margen 50% -> 1800
        self.assertEqual(p.unit_price, Decimal("1800.00"))

    def test_redondeo_precio(self):
        p = make_producto(margin_percent=Decimal("0"), round_to=Decimal("100"))
        ProductoFilamentLine.objects.create(
            producto=p, filament=self.fil, grams_used=Decimal("123")
        )
        # material 123*10=1230 + máquina 200 = 1430 -> redondea a 1400
        self.assertEqual(p.unit_price, Decimal("1400.00"))


class PresupuestoTotalTests(TestCase):
    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("10000"),
        )
        self.p = make_producto(margin_percent=Decimal("0"))
        ProductoFilamentLine.objects.create(
            producto=self.p, filament=self.fil, grams_used=Decimal("100")
        )  # unit_price = 1000 + 200 = 1200

    def test_item_congela_precio(self):
        pres = Presupuesto.objects.create(client_name="Cliente")
        item = PresupuestoItem.objects.create(
            presupuesto=pres, producto=self.p, quantity=2
        )
        self.assertEqual(item.unit_price, Decimal("1200.00"))
        # Si cambia el costeo del producto, el precio del item queda congelado.
        self.p.machine_cost_per_hour = Decimal("999")
        self.p.save()
        item.refresh_from_db()
        self.assertEqual(item.effective_unit_price, Decimal("1200.00"))
        self.assertEqual(item.line_total, Decimal("2400.00"))

    def test_total_con_costo_fijo_y_redondeo(self):
        pres = Presupuesto.objects.create(
            client_name="Cliente", fixed_cost=Decimal("300"), round_to=Decimal("100")
        )
        PresupuestoItem.objects.create(presupuesto=pres, producto=self.p, quantity=1)
        # items 1200 + fijo 300 = 1500 -> redondea a 1500
        self.assertEqual(pres.items_total, Decimal("1200.00"))
        self.assertEqual(pres.total, Decimal("1500.00"))


class StatusTransitionTests(TestCase):
    """I1/I2: cambiar estado setea fechas y dispara producción."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("10000"),
        )
        self.maquina = Maquina.objects.create(name="Ender", is_active=True)
        self.p = make_producto()
        ProductoFilamentLine.objects.create(
            producto=self.p, filament=self.fil, grams_used=Decimal("100")
        )
        self.pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(presupuesto=self.pres, producto=self.p, quantity=1)

    def test_sent_setea_sent_at(self):
        self.pres.status = Presupuesto.Status.SENT
        self.pres.apply_status_change(Presupuesto.Status.DRAFT)
        self.pres.refresh_from_db()
        self.assertIsNotNone(self.pres.sent_at)
        self.assertIsNone(self.pres.approved_at)

    def test_approved_setea_fecha_y_genera_cola(self):
        self.pres.status = Presupuesto.Status.APPROVED
        self.pres.apply_status_change(Presupuesto.Status.SENT)
        self.pres.refresh_from_db()
        self.assertIsNotNone(self.pres.approved_at)
        self.assertEqual(self.pres.jobs.count(), 1)
        self.assertIsNotNone(self.pres.due_date)

    def test_completed_setea_fechas(self):
        self.pres.status = Presupuesto.Status.COMPLETED
        self.pres.apply_status_change(Presupuesto.Status.IN_PRODUCTION)
        self.pres.refresh_from_db()
        self.assertIsNotNone(self.pres.production_finished_at)
        self.assertIsNotNone(self.pres.completed_at)

    def test_fechas_idempotentes(self):
        self.pres.status = Presupuesto.Status.APPROVED
        self.pres.apply_status_change(Presupuesto.Status.DRAFT)
        self.pres.refresh_from_db()
        first = self.pres.approved_at
        # Volver a "aplicar" no pisa la fecha ni duplica jobs.
        self.pres.apply_status_change(Presupuesto.Status.SENT)
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.approved_at, first)
        self.assertEqual(self.pres.jobs.count(), 1)

    def test_approve_action_sigue_funcionando(self):
        shortages = self.pres.approve()
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.status, Presupuesto.Status.APPROVED)
        self.assertIsNotNone(self.pres.approved_at)
        self.assertEqual(self.pres.jobs.count(), 1)
        self.assertEqual(shortages, [])


class StatusRollupFromJobsTests(TestCase):
    """El estado del presupuesto sigue a la cola de producción."""

    def setUp(self):
        from production.models import ProductionJob

        self.PJ = ProductionJob
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("10000"),
        )
        self.maquina = Maquina.objects.create(name="Ender", is_active=True)
        self.p = make_producto()
        ProductoFilamentLine.objects.create(
            producto=self.p, filament=self.fil, grams_used=Decimal("100")
        )
        self.pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(presupuesto=self.pres, producto=self.p, quantity=1)
        PresupuestoItem.objects.create(presupuesto=self.pres, producto=self.p, quantity=1)
        self.pres.approve()
        self.pres.refresh_from_db()
        self.jobs = list(self.pres.jobs.all())
        self.assertEqual(len(self.jobs), 2)

    def test_todos_en_cola_no_avanza(self):
        changed = self.pres.sync_status_from_jobs()
        self.pres.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(self.pres.status, Presupuesto.Status.APPROVED)

    def test_un_trabajo_imprimiendo_pasa_a_en_produccion(self):
        j = self.jobs[0]
        j.status = self.PJ.Status.PRINTING
        j.save()
        self.assertTrue(self.pres.sync_status_from_jobs())
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.status, Presupuesto.Status.IN_PRODUCTION)
        self.assertIsNotNone(self.pres.production_started_at)

    def test_todos_impresos_pasa_a_completado(self):
        for j in self.jobs:
            j.status = self.PJ.Status.DONE
            j.save()
        self.assertTrue(self.pres.sync_status_from_jobs())
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.status, Presupuesto.Status.COMPLETED)
        self.assertIsNotNone(self.pres.production_finished_at)
        self.assertIsNotNone(self.pres.completed_at)

    def test_no_retrocede(self):
        # Forzamos COMPLETED y luego "revertimos" un job: no debe retroceder.
        for j in self.jobs:
            j.status = self.PJ.Status.DONE
            j.save()
        self.pres.sync_status_from_jobs()
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.status, Presupuesto.Status.COMPLETED)
        self.jobs[0].status = self.PJ.Status.PENDING
        self.jobs[0].save()
        changed = self.pres.sync_status_from_jobs()
        self.pres.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(self.pres.status, Presupuesto.Status.COMPLETED)

    def test_cancelados_no_bloquean_completado(self):
        self.jobs[0].status = self.PJ.Status.DONE
        self.jobs[0].save()
        self.jobs[1].status = self.PJ.Status.CANCELLED
        self.jobs[1].save()
        self.assertTrue(self.pres.sync_status_from_jobs())
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.status, Presupuesto.Status.COMPLETED)
