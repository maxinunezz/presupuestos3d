from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Presupuesto, Producto, ProductoFilamentLine
from inventory.models import Filament, StockMovement

from .models import Maquina, ProductionJob
from .scheduler import next_loadable, recommend_machine


def make_producto(multicolor=False):
    return Producto.objects.create(
        name="Pieza",
        print_time_hours=Decimal("2"),
        is_multicolor=multicolor,
    )


class ConsumeStockTests(TestCase):
    """I3: el movimiento se registra por lo realmente descontado."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("1000"),
        )
        self.producto = make_producto()
        ProductoFilamentLine.objects.create(
            producto=self.producto, filament=self.fil, grams_used=Decimal("100")
        )
        self.pres = Presupuesto.objects.create(client_name="Cliente")

    def _job(self, quantity):
        return ProductionJob.objects.create(
            presupuesto=self.pres, producto=self.producto, quantity=quantity
        )

    def test_consume_descuenta_y_registra_total(self):
        job = self._job(3)  # 300 g
        job.consume_stock()
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("700"))
        mov = StockMovement.objects.get(filament=self.fil)
        self.assertEqual(mov.quantity, Decimal("-300.00"))
        self.assertTrue(job.stock_consumed)

    def test_consume_es_idempotente(self):
        job = self._job(2)  # 200 g
        job.consume_stock()
        job.consume_stock()
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("800"))
        self.assertEqual(StockMovement.objects.filter(filament=self.fil).count(), 1)

    def test_consume_con_stock_insuficiente_registra_lo_real(self):
        self.fil.stock_grams = Decimal("250")
        self.fil.save()
        job = self._job(3)  # pide 300 g, hay 250
        job.consume_stock()
        self.fil.refresh_from_db()
        # No queda negativo.
        self.assertEqual(self.fil.stock_grams, Decimal("0"))
        mov = StockMovement.objects.get(filament=self.fil)
        # El movimiento refleja lo realmente consumido (250), no los 300.
        self.assertEqual(mov.quantity, Decimal("-250.00"))
        self.assertIn("faltaron", mov.note)


class MulticolorValidationTests(TestCase):
    def setUp(self):
        self.ams = Maquina.objects.create(name="Bambu", supports_multicolor=True)
        self.ender = Maquina.objects.create(name="Ender", supports_multicolor=False)
        self.pres = Presupuesto.objects.create(client_name="Cliente")

    def test_clean_rechaza_multicolor_en_no_ams(self):
        producto = make_producto(multicolor=True)
        job = ProductionJob(
            presupuesto=self.pres, producto=producto, quantity=1, machine=self.ender
        )
        with self.assertRaises(ValidationError):
            job.clean()

    def test_clean_acepta_multicolor_en_ams(self):
        producto = make_producto(multicolor=True)
        job = ProductionJob(
            presupuesto=self.pres, producto=producto, quantity=1, machine=self.ams
        )
        job.clean()  # no levanta

    def test_recommend_multicolor_solo_maquinas_ams(self):
        machine, _ = recommend_machine(
            Decimal("2"), requires_multicolor=True
        )
        self.assertEqual(machine, self.ams)

    def test_recommend_sin_ams_devuelve_none(self):
        self.ams.delete()
        machine, _ = recommend_machine(
            Decimal("2"), requires_multicolor=True
        )
        self.assertIsNone(machine)


class MulticolorToggleAdminTests(TestCase):
    """M3: apagar supports_multicolor libera los jobs multicolor de esa máquina."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        self.ams = Maquina.objects.create(name="Bambu", supports_multicolor=True)
        self.pres = Presupuesto.objects.create(client_name="Cliente")
        self.multi = make_producto(multicolor=True)
        self.normal = make_producto(multicolor=False)
        self.job_multi = ProductionJob.objects.create(
            presupuesto=self.pres, producto=self.multi, quantity=1, machine=self.ams
        )
        self.job_normal = ProductionJob.objects.create(
            presupuesto=self.pres, producto=self.normal, quantity=1, machine=self.ams
        )

    def test_apagar_multicolor_libera_solo_jobs_multicolor(self):
        url = reverse("admin:production_maquina_change", args=[self.ams.pk])
        self.client.post(
            url,
            {
                "name": "Bambu",
                "is_active": "on",
                # supports_multicolor desmarcado (ausente => False)
                "notes": "",
                "_save": "Save",
            },
        )
        self.job_multi.refresh_from_db()
        self.job_normal.refresh_from_db()
        self.ams.refresh_from_db()
        self.assertFalse(self.ams.supports_multicolor)
        # El job multicolor quedó sin máquina; el normal sigue asignado.
        self.assertIsNone(self.job_multi.machine_id)
        self.assertEqual(self.job_normal.machine_id, self.ams.id)


class SchedulerWindowTests(TestCase):
    def _aware(self, h, m):
        naive = datetime.combine(timezone.localdate(), time(h, m))
        return timezone.make_aware(naive, timezone.get_current_timezone())

    def test_dentro_de_ventana_no_cambia(self):
        dt = self._aware(10, 0)
        self.assertEqual(next_loadable(dt), dt)

    def test_madrugada_salta_a_las_7(self):
        dt = self._aware(3, 0)
        result = timezone.localtime(next_loadable(dt))
        self.assertEqual((result.hour, result.minute), (7, 0))
        self.assertEqual(result.date(), timezone.localdate())

    def test_de_noche_salta_al_dia_siguiente(self):
        dt = self._aware(23, 30)
        result = timezone.localtime(next_loadable(dt))
        self.assertEqual(result.hour, 7)
        self.assertGreater(result.date(), timezone.localdate())
