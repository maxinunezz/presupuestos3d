from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Pieza, PiezaFilamentLine, Presupuesto, Producto
from inventory.models import Filament, StockMovement

from .models import (
    Herramienta,
    HerramientaCategoria,
    HistorialImpresion,
    MantenimientoHerramienta,
    Maquina,
    ProductionJob,
    TramoImpresion,
)
from .scheduler import next_loadable, rebalance_idle_machines, recommend_machine


def make_producto(multicolor=False):
    return Producto.objects.create(
        name="Pieza",
        is_multicolor=multicolor,
        waste_percent=Decimal("0"),
        # Diseño listo por default: estos tests no ejercitan esa regla, solo
        # necesitan poder aprobar/encolar producción.
        diseno_listo=True,
    )


def add_pieza(producto, filament, grams, print_hours=Decimal("2"), name="Pieza principal"):
    """Crea una pieza con sus horas de máquina y una línea de filamento.
    Por defecto: 1 unidad por producto, 1 pieza por gcode (1 corrida)."""
    pieza = Pieza.objects.create(
        producto=producto,
        name=name,
        units_needed=1,
        pieces_per_gcode=1,
        print_time_minutes=print_hours * 60,
    )
    PiezaFilamentLine.objects.create(pieza=pieza, filament=filament, grams_used=grams)
    return pieza


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
        add_pieza(self.producto, self.fil, Decimal("100"))
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

    def test_consume_con_stock_insuficiente_queda_negativo(self):
        self.fil.stock_grams = Decimal("250")
        self.fil.save()
        job = self._job(3)  # pide 300 g, hay 250
        job.consume_stock()
        self.fil.refresh_from_db()
        # La producción descuenta el consumo completo aunque quede en negativo,
        # así el faltante queda visible en el pronóstico de compra.
        self.assertEqual(self.fil.stock_grams, Decimal("-50.00"))
        mov = StockMovement.objects.get(filament=self.fil)
        # El movimiento refleja los gramos completos consumidos (300).
        self.assertEqual(mov.quantity, Decimal("-300.00"))
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
                "cost_per_hour": "0",
                "notes": "",
                "historial-TOTAL_FORMS": "0",
                "historial-INITIAL_FORMS": "0",
                "historial-MIN_NUM_FORMS": "0",
                "historial-MAX_NUM_FORMS": "1000",
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


class DepreciacionTests(TestCase):
    """La depreciación = horas impresas acumuladas × costo por hora."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("10000"),
        )
        self.maquina = Maquina.objects.create(
            name="Ender", cost_per_hour=Decimal("500")
        )
        self.producto = make_producto()
        # 2 h de impresión por corrida, 1 corrida por pieza.
        add_pieza(self.producto, self.fil, Decimal("50"), print_hours=Decimal("2"))
        self.pres = Presupuesto.objects.create(client_name="Cliente")

    def _job(self, quantity, pieza):
        return ProductionJob.objects.create(
            presupuesto=self.pres,
            producto=self.producto,
            pieza=pieza,
            quantity=quantity,
            machine=self.maquina,
        )

    def test_recalc_suma_solo_trabajos_terminados(self):
        pieza = self.producto.piezas.first()
        done = self._job(3, pieza)  # 3 corridas × 2 h = 6 h
        done.status = ProductionJob.Status.DONE
        done.save(update_fields=["status"])
        self._job(2, pieza)  # en cola: no suma

        self.maquina.recalc_printed_hours()
        self.maquina.refresh_from_db()
        self.assertEqual(self.maquina.total_hours_printed, Decimal("6.00"))
        self.assertEqual(self.maquina.accumulated_depreciation, Decimal("3000.00"))

    def test_recalc_es_idempotente(self):
        pieza = self.producto.piezas.first()
        done = self._job(1, pieza)  # 2 h
        done.status = ProductionJob.Status.DONE
        done.save(update_fields=["status"])
        self.maquina.recalc_printed_hours()
        self.maquina.recalc_printed_hours()
        self.maquina.refresh_from_db()
        self.assertEqual(self.maquina.total_hours_printed, Decimal("2.00"))

    def test_depreciacion_sin_horas_es_cero(self):
        self.assertEqual(self.maquina.accumulated_depreciation, Decimal("0.00"))


class ImpresionObsoletaTests(TestCase):
    """Marcar una impresión obsoleta: pierde scrap, devuelve el resto, reimprime."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("1000"),
        )
        self.producto = make_producto()
        # 100 g por corrida, 1 corrida por pieza, 1 unidad por producto.
        self.pieza = add_pieza(self.producto, self.fil, Decimal("100"))
        self.pres = Presupuesto.objects.create(client_name="Cliente")

    def _pieza_job(self, quantity=1, status=ProductionJob.Status.PRINTING):
        job = ProductionJob.objects.create(
            presupuesto=self.pres,
            producto=self.producto,
            pieza=self.pieza,
            quantity=quantity,
            status=status,
        )
        job.consume_stock()  # descuenta el filamento (como al aprobar)
        return job

    def test_obsoleta_devuelve_resto_y_pierde_scrap(self):
        job = self._pieza_job()  # consume 100 g -> stock 900
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("900.00"))

        summary = job.mark_obsolete(Decimal("30"))  # pierde 30, vuelven 70

        self.fil.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("970.00"))
        self.assertEqual(summary["scrap"], Decimal("30.00"))
        self.assertEqual(summary["returned"][0]["grams"], Decimal("70.00"))
        # Vuelve a la cola y queda sin material descontado (se reconsume al reimprimir).
        self.assertEqual(job.status, ProductionJob.Status.PENDING)
        self.assertFalse(job.stock_consumed)
        mov = StockMovement.objects.filter(
            reason=StockMovement.Reason.REPRINT_FAILURE
        ).get()
        self.assertEqual(mov.quantity, Decimal("70.00"))

    def test_reimpresion_vuelve_a_consumir_neto_total_mas_scrap(self):
        job = self._pieza_job()  # -100 -> 900
        job.mark_obsolete(Decimal("30"))  # +70 -> 970
        # La reimpresión vuelve a descontar el total al terminar.
        job.consume_stock()  # -100 -> 870
        self.fil.refresh_from_db()
        # Neto: 1000 - 100 - 30 = 870 (total reimpreso + scrap perdido).
        self.assertEqual(self.fil.stock_grams, Decimal("870.00"))

    def test_scrap_mayor_al_total_se_limita(self):
        job = self._pieza_job()  # -100 -> 900
        summary = job.mark_obsolete(Decimal("500"))  # se limita a 100
        self.fil.refresh_from_db()
        # Se pierde todo: no vuelve nada.
        self.assertEqual(summary["scrap"], Decimal("100.00"))
        self.assertEqual(self.fil.stock_grams, Decimal("900.00"))

    def test_no_se_puede_obsoletar_trabajo_terminado(self):
        job = self._pieza_job(status=ProductionJob.Status.DONE)
        with self.assertRaises(ValueError):
            job.mark_obsolete(Decimal("10"))

    def test_scrap_multicolor_no_descuadra_por_redondeo(self):
        # Pieza con 3 líneas que no dividen exacto: la suma de lo devuelto debe
        # ser total − scrap exacto (la última línea absorbe el residuo).
        fil2 = Filament.objects.create(
            brand="M", material_type=Filament.MaterialType.PETG, color="C2",
            cost_per_kg=Decimal("10000"), stock_grams=Decimal("1000"),
        )
        fil3 = Filament.objects.create(
            brand="M", material_type=Filament.MaterialType.ABS, color="C3",
            cost_per_kg=Decimal("10000"), stock_grams=Decimal("1000"),
        )
        producto = make_producto()
        pieza = add_pieza(producto, self.fil, Decimal("33.33"), name="Multi")
        PiezaFilamentLine.objects.create(pieza=pieza, filament=fil2, grams_used=Decimal("33.33"))
        PiezaFilamentLine.objects.create(pieza=pieza, filament=fil3, grams_used=Decimal("33.34"))
        job = ProductionJob.objects.create(
            presupuesto=self.pres, producto=producto, pieza=pieza,
            quantity=1, status=ProductionJob.Status.PRINTING,
        )
        job.consume_stock()
        summary = job.mark_obsolete(Decimal("50"))  # total 100, scrap 50
        total_devuelto = sum(r["grams"] for r in summary["returned"])
        # 100 − 50 = 50 exacto, sin descuadre de centésimas.
        self.assertEqual(total_devuelto, Decimal("50.00"))

    def test_accion_admin_marca_obsoleta(self):
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        job = self._pieza_job()  # -100 -> 900
        url = reverse("admin:production_productionjob_changelist")
        # Segundo paso: confirma con los gramos perdidos.
        self.client.post(
            url,
            {
                "action": "marcar_obsoleta",
                "_selected_action": [job.pk],
                "apply_obsoleta": "1",
                f"scrap_{job.pk}": "40",
            },
        )
        job.refresh_from_db()
        self.fil.refresh_from_db()
        self.assertEqual(job.status, ProductionJob.Status.PENDING)
        self.assertFalse(job.stock_consumed)
        # Volvieron 60 g (100 - 40): 900 + 60 = 960.
        self.assertEqual(self.fil.stock_grams, Decimal("960.00"))


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


class HistorialImpresionTests(TestCase):
    """Al imprimirse un trabajo se guarda en el historial de su máquina."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("1000"),
        )
        self.producto = make_producto()
        self.pieza = add_pieza(self.producto, self.fil, Decimal("100"))
        self.maquina = Maquina.objects.create(name="Ender")
        self.pres = Presupuesto.objects.create(client_name="Cliente")

    def _job(self, quantity=1, machine=None):
        return ProductionJob.objects.create(
            presupuesto=self.pres,
            producto=self.producto,
            pieza=self.pieza,
            quantity=quantity,
            machine=machine,
        )

    def test_register_history_crea_registro(self):
        job = self._job(quantity=2, machine=self.maquina)
        registros = job.register_history()
        self.assertEqual(len(registros), 1)
        registro = registros[0]
        self.assertEqual(self.maquina.historial.count(), 1)
        self.assertEqual(registro.maquina, self.maquina)
        self.assertEqual(registro.cantidad, 2)
        self.assertEqual(registro.presupuesto, self.pres)
        self.assertIn("Cliente", registro.titulo)
        # Por defecto se registra como Impreso.
        self.assertEqual(registro.estado, HistorialImpresion.Estado.IMPRESO)
        job.refresh_from_db()
        self.assertTrue(job.history_added)

    def test_register_history_cancelado(self):
        job = self._job(machine=self.maquina)
        registros = job.register_history(estado=HistorialImpresion.Estado.CANCELADO)
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0].estado, HistorialImpresion.Estado.CANCELADO)
        self.assertEqual(self.maquina.historial.count(), 1)

    def test_cancelar_presupuesto_registra_en_historial(self):
        # Un trabajo en cola con máquina asignada; al cancelar el pedido debe
        # quedar registrado en el historial de su máquina como Cancelado.
        self.pres.stock_provisioned = True
        self.pres.status = Presupuesto.Status.APPROVED
        self.pres.save()
        job = self._job(machine=self.maquina)
        self.pres.cancel()
        job.refresh_from_db()
        self.assertEqual(job.status, ProductionJob.Status.CANCELLED)
        self.assertEqual(self.maquina.historial.count(), 1)
        registro = self.maquina.historial.first()
        self.assertEqual(registro.estado, HistorialImpresion.Estado.CANCELADO)
        self.assertTrue(job.history_added)

    def test_register_history_es_idempotente(self):
        job = self._job(machine=self.maquina)
        job.register_history()
        job.register_history()
        self.assertEqual(self.maquina.historial.count(), 1)

    def test_register_history_sin_maquina_no_hace_nada(self):
        job = self._job(machine=None)
        self.assertEqual(job.register_history(), [])
        self.assertEqual(HistorialImpresion.objects.count(), 0)
        self.assertFalse(job.history_added)

    def test_register_history_se_atribuye_a_la_maquina_correcta(self):
        otra = Maquina.objects.create(name="Bambu")
        self._job(machine=self.maquina).register_history()
        self._job(machine=otra).register_history()
        self.assertEqual(self.maquina.historial.count(), 1)
        self.assertEqual(otra.historial.count(), 1)

    def test_mark_obsolete_resetea_history_added(self):
        job = self._job(machine=self.maquina)
        job.consume_stock()
        job.register_history()
        self.assertTrue(job.history_added)
        job.mark_obsolete(Decimal("0"))
        job.refresh_from_db()
        self.assertFalse(job.history_added)

    def _save_related(self):
        """Simula el save_related del PresupuestoAdmin (efectos de los inlines)."""
        from django.contrib.admin.sites import site
        from django.contrib.auth import get_user_model
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from budgets.admin import PresupuestoAdmin

        request = RequestFactory().post("/")
        request.user = get_user_model().objects.create_superuser("hadmin", password="x")
        request.session = "session"
        request._messages = FallbackStorage(request)
        request._old_presupuesto_status = self.pres.status  # sin cambio de estado
        form = type("F", (), {"instance": self.pres, "save_m2m": lambda self=None: None})()
        PresupuestoAdmin(Presupuesto, site).save_related(request, form, [], True)

    def test_done_desde_inline_registra_impreso(self):
        job = self._job(machine=self.maquina)
        job.status = ProductionJob.Status.DONE
        job.save(update_fields=["status"])
        self._save_related()
        self.assertEqual(self.maquina.historial.count(), 1)
        self.assertEqual(
            self.maquina.historial.first().estado,
            HistorialImpresion.Estado.IMPRESO,
        )

    def test_cancelado_desde_inline_registra_cancelado(self):
        job = self._job(machine=self.maquina)
        job.status = ProductionJob.Status.CANCELLED
        job.save(update_fields=["status"])
        self._save_related()
        self.assertEqual(self.maquina.historial.count(), 1)
        self.assertEqual(
            self.maquina.historial.first().estado,
            HistorialImpresion.Estado.CANCELADO,
        )


class GcodeRunsProgressTests(TestCase):
    """"X/Y corridas de gcode": progreso por corrida hasta completar el trabajo."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("10000"),
        )
        self.producto = make_producto()
        # 2 piezas por corrida, 7 necesarias -> ceil(7/2) = 4 corridas.
        self.pieza = Pieza.objects.create(
            producto=self.producto,
            name="Pin traba dorsal",
            units_needed=7,
            pieces_per_gcode=2,
            print_time_minutes=Decimal("30"),
        )
        PiezaFilamentLine.objects.create(
            pieza=self.pieza, filament=self.fil, grams_used=Decimal("10")
        )
        self.pres = Presupuesto.objects.create(client_name="Cliente")
        self.maquina = Maquina.objects.create(name="Ender")
        self.job = ProductionJob.objects.create(
            presupuesto=self.pres,
            producto=self.producto,
            pieza=self.pieza,
            quantity=7,
            machine=self.maquina,
        )

    def test_gcode_runs_y_advance_run(self):
        self.assertEqual(self.job.gcode_runs, 4)
        self.assertEqual(self.job.completed_runs, 0)
        self.assertEqual(self.job.advance_run(), 1)
        self.assertEqual(self.job.advance_run(), 2)
        # No pasa del total aunque se llame de más.
        self.job.completed_runs = 4
        self.assertEqual(self.job.advance_run(), 4)

    def test_mark_obsolete_resetea_completed_runs(self):
        self.job.consume_stock()
        self.job.completed_runs = 2
        self.job.save(update_fields=["completed_runs"])
        self.job.mark_obsolete(Decimal("0"))
        self.job.refresh_from_db()
        self.assertEqual(self.job.completed_runs, 0)

    def _login(self):
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    def test_primera_corrida_pasa_a_imprimiendo_y_suma_progreso(self):
        self._login()
        url = reverse("admin:production_colaproduccion_marcar_corrida", args=[self.job.pk])
        self.client.post(url)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ProductionJob.Status.PRINTING)
        self.assertEqual(self.job.completed_runs, 1)
        self.assertIsNotNone(self.job.started_at)

    def test_ultima_corrida_completa_el_trabajo_y_dispara_efectos(self):
        self._login()
        url = reverse("admin:production_colaproduccion_marcar_corrida", args=[self.job.pk])
        for _ in range(4):
            self.client.post(url)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ProductionJob.Status.DONE)
        self.assertEqual(self.job.completed_runs, 4)
        self.assertTrue(self.job.stock_consumed)
        self.assertIsNotNone(self.job.finished_at)
        # 4 corridas × 2 piezas por corrida = 8 unidades salidas, pidieron 7:
        # 1 unidad sobrante va al stock de la pieza.
        self.pieza.refresh_from_db()
        self.assertEqual(self.pieza.stock_quantity, 1)
        self.maquina.refresh_from_db()
        self.assertGreater(self.maquina.total_hours_printed, Decimal("0"))
        # No sigue sumando corridas pasado el total.
        self.client.post(url)
        self.job.refresh_from_db()
        self.assertEqual(self.job.completed_runs, 4)

    def test_via_tablero_tambien_funciona(self):
        self._login()
        url = reverse("admin:production_tablero_marcar_corrida", args=[self.job.pk])
        self.client.post(url)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ProductionJob.Status.PRINTING)
        self.assertEqual(self.job.completed_runs, 1)

    def test_job_sin_corridas_no_rompe(self):
        self._login()
        self.job.quantity = 0
        self.job.save(update_fields=["quantity"])
        url = reverse("admin:production_colaproduccion_marcar_corrida", args=[self.job.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.completed_runs, 0)

    def test_empezar_pasa_a_imprimiendo_sin_sumar_corridas(self):
        self._login()
        url = reverse("admin:production_colaproduccion_empezar", args=[self.job.pk])
        self.client.post(url)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ProductionJob.Status.PRINTING)
        self.assertEqual(self.job.completed_runs, 0)
        self.assertIsNotNone(self.job.started_at)

    def test_empezar_via_tablero_tambien_funciona(self):
        self._login()
        url = reverse("admin:production_tablero_empezar", args=[self.job.pk])
        self.client.post(url)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ProductionJob.Status.PRINTING)

    def test_empezar_no_hace_nada_si_ya_no_esta_en_cola(self):
        self._login()
        self.job.status = ProductionJob.Status.PRINTING
        self.job.save(update_fields=["status"])
        url = reverse("admin:production_colaproduccion_empezar", args=[self.job.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ProductionJob.Status.PRINTING)


class RebalanceIdleMachinesTests(TestCase):
    """Si una máquina queda libre y otra tiene cola de sobra, le pasa trabajo."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("100000"),
        )
        self.a1n1 = Maquina.objects.create(name="A1N1 Bambu Lab", supports_multicolor=True)
        self.a1n2 = Maquina.objects.create(name="A1N2 Bambu Lab", supports_multicolor=True)
        self.pres = Presupuesto.objects.create(client_name="Cliente")

    def _job(self, machine, status=ProductionJob.Status.PENDING, order=0, multicolor=False):
        producto = make_producto(multicolor=multicolor)
        pieza = add_pieza(producto, self.fil, Decimal("10"))
        if multicolor:
            pieza.requires_ams = True
            pieza.save(update_fields=["requires_ams"])
        return ProductionJob.objects.create(
            presupuesto=self.pres,
            producto=producto,
            pieza=pieza,
            quantity=1,
            machine=machine,
            status=status,
            order=order,
        )

    def test_pasa_trabajo_pendiente_a_maquina_libre(self):
        # Escenario del usuario: A1N1 libre, A1N2 con uno imprimiendo y otro
        # en cola detrás.
        printing = self._job(self.a1n2, status=ProductionJob.Status.PRINTING, order=0)
        pending = self._job(self.a1n2, status=ProductionJob.Status.PENDING, order=1)

        moved = rebalance_idle_machines()

        self.assertEqual(moved, [pending])
        pending.refresh_from_db()
        printing.refresh_from_db()
        self.assertEqual(pending.machine_id, self.a1n1.id)
        # El que ya estaba imprimiendo no se toca.
        self.assertEqual(printing.machine_id, self.a1n2.id)

    def test_no_roba_si_la_donante_solo_tiene_un_trabajo(self):
        # A1N2 tiene un solo trabajo: no la dejamos sin nada.
        only = self._job(self.a1n2, status=ProductionJob.Status.PRINTING)

        moved = rebalance_idle_machines()

        self.assertEqual(moved, [])
        only.refresh_from_db()
        self.assertEqual(only.machine_id, self.a1n2.id)

    def test_no_mueve_nada_si_ninguna_maquina_esta_libre(self):
        self._job(self.a1n1, status=ProductionJob.Status.PRINTING)
        self._job(self.a1n2, status=ProductionJob.Status.PRINTING)
        self._job(self.a1n2, status=ProductionJob.Status.PENDING, order=1)

        self.assertEqual(rebalance_idle_machines(), [])

    def test_respeta_compatibilidad_multicolor(self):
        # A1N1 (la única libre) no soporta multicolor; el único candidato
        # a moverse en A1N2 sí lo necesita -> no se mueve nada.
        self.a1n1.supports_multicolor = False
        self.a1n1.save(update_fields=["supports_multicolor"])
        printing = self._job(self.a1n2, status=ProductionJob.Status.PRINTING, order=0)
        pending = self._job(
            self.a1n2, status=ProductionJob.Status.PENDING, order=1, multicolor=True
        )

        moved = rebalance_idle_machines()

        self.assertEqual(moved, [])
        pending.refresh_from_db()
        self.assertEqual(pending.machine_id, self.a1n2.id)

    def _login(self):
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    def test_visitar_la_cola_dispara_el_rebalanceo(self):
        self._login()
        self._job(self.a1n2, status=ProductionJob.Status.PRINTING, order=0)
        pending = self._job(self.a1n2, status=ProductionJob.Status.PENDING, order=1)

        self.client.get(reverse("admin:production_colaproduccion_changelist"))

        pending.refresh_from_db()
        self.assertEqual(pending.machine_id, self.a1n1.id)

    def test_visitar_el_tablero_dispara_el_rebalanceo(self):
        self._login()
        self._job(self.a1n2, status=ProductionJob.Status.PRINTING, order=0)
        pending = self._job(self.a1n2, status=ProductionJob.Status.PENDING, order=1)

        self.client.get(reverse("admin:production_tablero_changelist"))

        pending.refresh_from_db()
        self.assertEqual(pending.machine_id, self.a1n1.id)

    def test_marcar_impreso_libera_la_maquina_y_reacomoda(self):
        # Al marcar el trabajo de A1N1 como Impreso, A1N1 queda libre; el
        # save_model del admin debería pasarle el pendiente de A1N2.
        self._login()
        done_soon = self._job(self.a1n1, status=ProductionJob.Status.PRINTING)
        self._job(self.a1n2, status=ProductionJob.Status.PRINTING, order=0)
        pending = self._job(self.a1n2, status=ProductionJob.Status.PENDING, order=1)

        url = reverse("admin:production_colaproduccion_marcar_impreso", args=[done_soon.pk])
        self.client.post(url)

        pending.refresh_from_db()
        self.assertEqual(pending.machine_id, self.a1n1.id)


class TramoImpresionTests(TestCase):
    """
    Reproduce el bug reportado: un trabajo de 9 corridas que arranca en una
    máquina y se reasigna a mitad de camino a otra. Las corridas hechas en
    cada máquina deben quedar repartidas (tramos), tanto en el historial
    ("Últimos impresos por máquina") como en las horas acumuladas de cada
    máquina — no todo atribuido a la máquina donde terminó.
    """

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("100000"),
        )
        self.producto = make_producto()
        # 1 corrida = 1 unidad, 1 hora de impresión por corrida.
        self.pieza = add_pieza(
            self.producto, self.fil, Decimal("10"), print_hours=Decimal("1")
        )
        self.pieza.pieces_per_gcode = 1
        self.pieza.save(update_fields=["pieces_per_gcode"])
        self.ender = Maquina.objects.create(name="Ender")
        self.a1n2 = Maquina.objects.create(name="A1N2", supports_multicolor=True)
        self.pres = Presupuesto.objects.create(client_name="Cliente")
        self.job = ProductionJob.objects.create(
            presupuesto=self.pres,
            producto=self.producto,
            pieza=self.pieza,
            quantity=9,
            machine=self.ender,
            status=ProductionJob.Status.PRINTING,
        )

    def _finish_on_a1n2(self):
        """Corre 3 corridas en la Ender, cambia a la A1N2 a mitad de camino
        (como hizo el usuario) y termina las 6 restantes ahí."""
        for _ in range(3):
            self.job.advance_run()
        self.job.machine = self.a1n2
        self.job.save(update_fields=["machine"])
        for _ in range(6):
            self.job.advance_run()

    def test_advance_run_acredita_corridas_a_la_maquina_actual(self):
        self._finish_on_a1n2()
        self.assertEqual(self.job.completed_runs, 9)
        tramos = list(self.job.tramos.order_by("started_at"))
        self.assertEqual(len(tramos), 2)
        self.assertEqual(tramos[0].machine_id, self.ender.id)
        self.assertEqual(tramos[0].runs, 3)
        self.assertFalse(tramos[0].is_open)
        self.assertEqual(tramos[1].machine_id, self.a1n2.id)
        self.assertEqual(tramos[1].runs, 6)

    def test_finalize_completa_corridas_no_registradas_en_maquina_actual(self):
        # Si el trabajo se marca Impreso sin pasar por "Corrida terminada"
        # para cada una (ej. de a una sola vez), las corridas que falten se
        # acreditan a la máquina actual.
        for _ in range(3):
            self.job.advance_run()
        self.job.machine = self.a1n2
        self.job.save(update_fields=["machine"])
        # No se llaman más advance_run(): quedan 6 corridas sin registrar.
        self.job.finalize_tramos()
        tramos = list(self.job.tramos.all())
        total_runs = sum(t.runs for t in tramos)
        self.assertEqual(total_runs, 9)
        self.assertTrue(all(not t.is_open for t in tramos))
        a1n2_runs = sum(t.runs for t in tramos if t.machine_id == self.a1n2.id)
        self.assertEqual(a1n2_runs, 6)

    def test_register_history_reparte_entre_las_dos_maquinas(self):
        self._finish_on_a1n2()
        self.job.status = ProductionJob.Status.DONE
        self.job.finished_at = timezone.now()
        self.job.save(update_fields=["status", "finished_at"])
        self.job.finalize_tramos()

        registros = self.job.register_history()
        self.assertEqual(len(registros), 2)
        by_machine = {r.maquina_id: r for r in registros}
        self.assertIn(self.ender.id, by_machine)
        self.assertIn(self.a1n2.id, by_machine)
        self.assertEqual(by_machine[self.ender.id].cantidad, 3)
        self.assertEqual(by_machine[self.ender.id].horas_impresion, Decimal("3.00"))
        self.assertEqual(by_machine[self.a1n2.id].cantidad, 6)
        self.assertEqual(by_machine[self.a1n2.id].horas_impresion, Decimal("6.00"))
        # La suma sigue dando el total del trabajo.
        self.assertEqual(
            sum(r.cantidad for r in registros), self.job.quantity
        )

    def test_recalc_printed_hours_reparte_entre_las_dos_maquinas(self):
        self._finish_on_a1n2()
        self.job.status = ProductionJob.Status.DONE
        self.job.finished_at = timezone.now()
        self.job.save(update_fields=["status", "finished_at"])
        self.job.finalize_tramos()

        self.ender.recalc_printed_hours()
        self.a1n2.recalc_printed_hours()
        self.ender.refresh_from_db()
        self.a1n2.refresh_from_db()
        self.assertEqual(self.ender.total_hours_printed, Decimal("3.00"))
        self.assertEqual(self.a1n2.total_hours_printed, Decimal("6.00"))

    def test_job_sin_tramos_sigue_contando_entero_en_su_maquina_final(self):
        # Compatibilidad: un trabajo DONE de antes de que existiera el
        # sistema de tramos (nunca se le creó ninguno) se sigue contando
        # entero en su máquina final, como antes.
        self.job.status = ProductionJob.Status.DONE
        self.job.save(update_fields=["status"])
        self.assertFalse(self.job.tramos.exists())

        self.ender.recalc_printed_hours()
        self.ender.refresh_from_db()
        self.assertEqual(self.ender.total_hours_printed, Decimal("9.00"))

        registros = self.job.register_history()
        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0].maquina_id, self.ender.id)
        self.assertEqual(registros[0].cantidad, 9)

    def test_mark_obsolete_descarta_los_tramos(self):
        self.job.consume_stock()
        for _ in range(3):
            self.job.advance_run()
        self.job.machine = self.a1n2
        self.job.save(update_fields=["machine"])
        self.assertTrue(self.job.tramos.exists())

        self.job.mark_obsolete(Decimal("0"))

        self.assertFalse(self.job.tramos.exists())
        self.assertEqual(self.job.completed_runs, 0)


class MachineChangeValidationTests(TestCase):
    """
    Tier 1 del guardarraíl: cambiar la máquina de un trabajo Imprimiendo
    desde el listado (edición rápida list_editable) queda bloqueado; hay que
    abrirlo (o usar "Cambiar de máquina" desde la cola/tablero) para
    reasignarlo de forma prolija.
    """

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("100000"),
        )
        self.producto = make_producto()
        self.pieza = add_pieza(self.producto, self.fil, Decimal("10"))
        self.ender = Maquina.objects.create(name="Ender")
        self.a1n2 = Maquina.objects.create(name="A1N2", supports_multicolor=True)
        self.pres = Presupuesto.objects.create(client_name="Cliente")
        self.job = ProductionJob.objects.create(
            presupuesto=self.pres,
            producto=self.producto,
            pieza=self.pieza,
            quantity=3,
            machine=self.ender,
            status=ProductionJob.Status.PRINTING,
        )
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    def _post_list_editable(self, machine_id):
        url = reverse("admin:production_productionjob_changelist")
        return self.client.post(
            url,
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(self.job.pk),
                "form-0-machine": str(machine_id),
                "form-0-order": "0",
                "form-0-status": ProductionJob.Status.PRINTING,
                "_save": "Save",
            },
        )

    def test_list_editable_no_cambia_maquina_de_trabajo_imprimiendo(self):
        self._post_list_editable(self.a1n2.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.machine_id, self.ender.id)

    def test_list_editable_permite_otros_cambios_si_no_toca_la_maquina(self):
        response = self.client.post(
            reverse("admin:production_productionjob_changelist"),
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(self.job.pk),
                "form-0-machine": str(self.ender.pk),
                "form-0-order": "5",
                "form-0-status": ProductionJob.Status.PRINTING,
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.order, 5)

    def test_list_editable_no_cambia_maquina_si_trabajo_esta_en_cola(self):
        # PENDING (no PRINTING): el guardarraíl solo aplica a Imprimiendo, un
        # trabajo que todavía no arrancó se puede reasignar sin problema.
        self.job.status = ProductionJob.Status.PENDING
        self.job.save(update_fields=["status"])
        url = reverse("admin:production_productionjob_changelist")
        self.client.post(
            url,
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-id": str(self.job.pk),
                "form-0-machine": str(self.a1n2.pk),
                "form-0-order": "0",
                "form-0-status": ProductionJob.Status.PENDING,
                "_save": "Save",
            },
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.machine_id, self.a1n2.id)

    def test_cambiar_maquina_desde_la_ficha_del_trabajo_si_funciona(self):
        # La vía sancionada (abrir el trabajo y guardarlo desde ahí, que es
        # a donde lleva el botón "Cambiar de máquina" de la cola/tablero) sí
        # permite reasignarlo mientras imprime.
        url = reverse("admin:production_productionjob_change", args=[self.job.pk])
        response = self.client.post(
            url,
            {
                "presupuesto": str(self.pres.pk),
                "producto": str(self.producto.pk),
                "pieza": str(self.pieza.pk),
                "quantity": "3",
                "machine": str(self.a1n2.pk),
                "order": "0",
                "status": ProductionJob.Status.PRINTING,
                "completed_runs": "0",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.machine_id, self.a1n2.id)


class HerramientaDepreciacionTests(TestCase):
    """Depreciación lineal opcional por artículo: mensual = (costo - residual)
    / vida útil, acumulada topeada al valor depreciable, y neto = costo -
    acumulada. Aislada en su propia sección: no la ejercita ningún test de
    Métricas/Panel de costos."""

    def setUp(self):
        self.categoria = HerramientaCategoria.objects.create(nombre="Equipos")

    def make_herramienta(self, **kwargs):
        defaults = dict(
            nombre="Termoformadora",
            categoria=self.categoria,
            costo_adquisicion=Decimal("120000"),
            fecha_compra=timezone.localdate(),
        )
        defaults.update(kwargs)
        return Herramienta.objects.create(**defaults)

    def test_se_deprecia_false_da_todo_cero(self):
        h = self.make_herramienta(se_deprecia=False)
        self.assertEqual(h.depreciacion_mensual, Decimal("0"))
        self.assertEqual(h.depreciacion_acumulada, Decimal("0"))
        self.assertEqual(h.valor_contable_neto, h.costo_adquisicion)
        self.assertFalse(h.esta_totalmente_depreciada)

    def test_depreciacion_mensual_lineal(self):
        h = self.make_herramienta(
            se_deprecia=True, vida_util_meses=12, valor_residual=Decimal("12000")
        )
        # (120000 - 12000) / 12 = 9000
        self.assertEqual(h.depreciacion_mensual, Decimal("9000.00"))

    def test_valor_residual_mayor_o_igual_al_costo_da_cero(self):
        h = self.make_herramienta(
            se_deprecia=True, vida_util_meses=12, valor_residual=Decimal("120000")
        )
        self.assertEqual(h.depreciacion_mensual, Decimal("0"))
        self.assertEqual(h.depreciacion_acumulada, Decimal("0"))

    def test_meses_transcurridos_mismo_dia_es_cero(self):
        h = self.make_herramienta(fecha_compra=timezone.localdate())
        self.assertEqual(h.meses_transcurridos, 0)

    def test_meses_transcurridos_fecha_futura_nunca_negativo(self):
        import datetime

        h = self.make_herramienta(
            fecha_compra=timezone.localdate() + datetime.timedelta(days=30)
        )
        self.assertEqual(h.meses_transcurridos, 0)

    def test_meses_transcurridos_usa_fecha_baja_si_hay(self):
        compra = timezone.localdate().replace(day=1)
        h = self.make_herramienta(
            fecha_compra=compra.replace(year=compra.year - 1),
            fecha_baja=compra,
            estado=Herramienta.Estado.BAJA,
        )
        self.assertEqual(h.meses_transcurridos, 12)

    def test_depreciacion_acumulada_topeada_al_valor_depreciable(self):
        compra = timezone.localdate().replace(day=1)
        h = self.make_herramienta(
            fecha_compra=compra.replace(year=compra.year - 5),
            se_deprecia=True,
            vida_util_meses=12,
            valor_residual=Decimal("12000"),
        )
        # Pasaron muchos más meses que la vida útil: la acumulada no puede
        # superar la base depreciable (120000 - 12000 = 108000).
        self.assertTrue(h.esta_totalmente_depreciada)
        self.assertEqual(h.depreciacion_acumulada, Decimal("108000.00"))
        self.assertEqual(h.valor_contable_neto, Decimal("12000.00"))

    def test_valor_contable_neto_sin_depreciar_es_el_costo_completo(self):
        h = self.make_herramienta(se_deprecia=True, vida_util_meses=12)
        self.assertEqual(h.valor_contable_neto, h.costo_adquisicion)


class HerramientaCleanValidationTests(TestCase):
    def setUp(self):
        self.categoria = HerramientaCategoria.objects.create(nombre="Equipos")

    def make_herramienta(self, **kwargs):
        defaults = dict(
            nombre="Termoformadora",
            categoria=self.categoria,
            costo_adquisicion=Decimal("1000"),
        )
        defaults.update(kwargs)
        return Herramienta(**defaults)

    def test_se_deprecia_sin_vida_util_falla(self):
        h = self.make_herramienta(se_deprecia=True, vida_util_meses=None)
        with self.assertRaises(ValidationError):
            h.clean()

    def test_se_deprecia_con_vida_util_pasa(self):
        h = self.make_herramienta(se_deprecia=True, vida_util_meses=24)
        h.clean()  # no lanza

    def test_cuota_actual_sin_cuotas_totales_falla(self):
        h = self.make_herramienta(cuota_actual=1, cuotas_totales=None)
        with self.assertRaises(ValidationError):
            h.clean()

    def test_cuotas_totales_sin_cuota_actual_falla(self):
        h = self.make_herramienta(cuota_actual=None, cuotas_totales=6)
        with self.assertRaises(ValidationError):
            h.clean()

    def test_cuota_actual_mayor_que_cuotas_totales_falla(self):
        h = self.make_herramienta(cuota_actual=7, cuotas_totales=6)
        with self.assertRaises(ValidationError):
            h.clean()

    def test_cuotas_consistentes_pasa(self):
        h = self.make_herramienta(cuota_actual=2, cuotas_totales=6)
        h.clean()  # no lanza


class HerramientaCategoriaTests(TestCase):
    """Categoría editable igual que `AggregateCategory`/`CategoriaGasto`:
    borrar una en uso está bloqueado para no perder la clasificación de lo
    ya cargado con ella."""

    def test_no_se_puede_borrar_una_categoria_en_uso(self):
        categoria = HerramientaCategoria.objects.create(nombre="Equipos")
        Herramienta.objects.create(
            nombre="Termoformadora",
            categoria=categoria,
            costo_adquisicion=Decimal("1000"),
        )
        with self.assertRaises(ProtectedError):
            categoria.delete()

    def test_se_puede_borrar_una_categoria_sin_uso(self):
        categoria = HerramientaCategoria.objects.create(nombre="Sin uso")
        categoria.delete()
        self.assertFalse(HerramientaCategoria.objects.filter(nombre="Sin uso").exists())


class MantenimientoHerramientaTests(TestCase):
    def test_se_puede_cargar_un_mantenimiento(self):
        categoria = HerramientaCategoria.objects.create(nombre="Equipos")
        herramienta = Herramienta.objects.create(
            nombre="Termoformadora",
            categoria=categoria,
            costo_adquisicion=Decimal("1000"),
        )
        MantenimientoHerramienta.objects.create(
            herramienta=herramienta,
            descripcion="Cambio de resistencia",
            costo=Decimal("5000"),
        )
        self.assertEqual(herramienta.mantenimientos.count(), 1)


class TableroProduccionKpisTests(TestCase):
    """El Tablero de producción muestra, arriba de todo, los KPIs de
    producción del mes (antes una sección del Panel de métricas): piezas/
    horas impresas, tasa de reimpresión, cumplimiento de entrega, y el
    gráfico/tabla de uso por máquina."""

    def setUp(self):
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        self.url = reverse("admin:production_tablero_changelist")

    def test_muestra_los_kpis_de_produccion(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Producción del mes")
        self.assertContains(resp, "Piezas impresas")
        self.assertContains(resp, "Horas impresas")
        self.assertContains(resp, "Tasa de reimpresión")
        self.assertContains(resp, "Cumplimiento de entrega")
        self.assertContains(resp, "Horas impresas por máquina")
        self.assertContains(resp, "Uso por máquina")
