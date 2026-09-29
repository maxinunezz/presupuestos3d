from datetime import datetime, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Pieza, PiezaFilamentLine, Presupuesto, Producto
from inventory.models import Filament, StockMovement

from .models import HistorialImpresion, Maquina, ProductionJob
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
        registro = job.register_history()
        self.assertIsNotNone(registro)
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
        registro = job.register_history(estado=HistorialImpresion.Estado.CANCELADO)
        self.assertIsNotNone(registro)
        self.assertEqual(registro.estado, HistorialImpresion.Estado.CANCELADO)
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
        self.assertIsNone(job.register_history())
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
