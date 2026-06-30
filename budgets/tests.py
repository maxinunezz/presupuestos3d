from decimal import Decimal

from django.test import TestCase

from inventory.models import Aggregate, Filament
from production.models import Maquina

from .models import (
    Pieza,
    PiezaFilamentLine,
    Presupuesto,
    PresupuestoItem,
    Producto,
    ProductoAggregateLine,
)


def make_producto(**kwargs):
    defaults = dict(
        name="Pieza",
        machine_cost_per_hour=Decimal("100"),
        labor_cost_per_hour=Decimal("0"),
        margin_percent=Decimal("0"),
    )
    defaults.update(kwargs)
    return Producto.objects.create(**defaults)


def add_pieza(producto, filament, grams, print_hours=Decimal("2"), name="Pieza principal"):
    """Crea una pieza con sus horas de máquina y una línea de filamento.
    Por defecto: 1 unidad por producto, 1 pieza por gcode (1 corrida)."""
    pieza = Pieza.objects.create(
        producto=producto,
        name=name,
        units_needed=1,
        pieces_per_gcode=1,
        print_time_hours=print_hours,
    )
    PiezaFilamentLine.objects.create(pieza=pieza, filament=filament, grams_used=grams)
    return pieza


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
        add_pieza(p, self.fil, Decimal("100"))
        # material 100g * $10 = 1000 ; máquina 2h * 100 = 200 ; total 1200
        self.assertEqual(p.material_cost, Decimal("1000.00"))
        self.assertEqual(p.machine_cost, Decimal("200.00"))
        self.assertEqual(p.unit_cost, Decimal("1200.00"))
        # margen 50% -> 1800
        self.assertEqual(p.unit_price, Decimal("1800.00"))

    def test_redondeo_precio(self):
        p = make_producto(margin_percent=Decimal("0"), round_to=Decimal("100"))
        add_pieza(p, self.fil, Decimal("123"))
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
        add_pieza(self.p, self.fil, Decimal("100"))  # unit_price = 1000 + 200 = 1200

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
        add_pieza(self.p, self.fil, Decimal("100"))
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
        result = self.pres.approve()
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.status, Presupuesto.Status.APPROVED)
        self.assertIsNotNone(self.pres.approved_at)
        self.assertEqual(self.pres.jobs.count(), 1)
        self.assertEqual(result["shortages"], [])
        self.assertEqual(result["from_stock"], [])


class AprobacionStockPiezasTests(TestCase):
    """Fase 2: al aprobar se descuenta el inventario, se usan las piezas en
    stock y solo se encola lo que falta imprimir."""

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
        self.pieza = add_pieza(self.p, self.fil, Decimal("100"))  # 1u, ppg 1, 100g

    def _pres(self, quantity):
        pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(
            presupuesto=pres, producto=self.p, quantity=quantity
        )
        return pres

    def test_material_se_descuenta_al_aprobar(self):
        from inventory.models import StockMovement

        pres = self._pres(2)
        pres.approve()
        self.fil.refresh_from_db()
        # 2 productos × 1 unidad × 100 g = 200 g, descontados YA al aprobar.
        self.assertEqual(self.fil.stock_grams, Decimal("9800.00"))
        self.assertTrue(pres.stock_provisioned)
        self.assertEqual(pres.jobs.count(), 1)
        self.assertEqual(pres.jobs.first().quantity, 2)
        self.assertTrue(pres.jobs.first().stock_consumed)
        self.assertTrue(
            StockMovement.objects.filter(
                reason=StockMovement.Reason.PRODUCTION, filament=self.fil
            ).exists()
        )

    def test_pieza_en_stock_no_se_encola_ni_consume_material(self):
        self.pieza.stock_quantity = 5
        self.pieza.save()
        pres = self._pres(3)
        result = pres.approve()
        # Todas salen de stock: no se imprime nada.
        self.assertEqual(pres.jobs.count(), 0)
        self.pieza.refresh_from_db()
        self.assertEqual(self.pieza.stock_quantity, 2)  # 5 - 3
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("10000"))  # sin imprimir
        self.assertEqual(result["from_stock"][0]["units"], 3)

    def test_stock_parcial_encola_el_resto(self):
        self.pieza.stock_quantity = 1
        self.pieza.save()
        pres = self._pres(3)
        pres.approve()
        self.pieza.refresh_from_db()
        self.assertEqual(self.pieza.stock_quantity, 0)
        self.assertEqual(pres.jobs.count(), 1)
        job = pres.jobs.first()
        self.assertEqual(job.quantity, 2)  # 3 - 1 de stock
        self.fil.refresh_from_db()
        # imprime 2 corridas × 100 g = 200 g
        self.assertEqual(self.fil.stock_grams, Decimal("9800.00"))

    def test_sobrante_de_gcode_va_a_stock_al_imprimir(self):
        # Pieza que saca 4 por corrida; se piden 3 -> imprime 1 corrida (4), sobra 1.
        pieza = Pieza.objects.create(
            producto=self.p, name="Tapa", units_needed=1, pieces_per_gcode=4,
            print_time_hours=Decimal("1"),
        )
        PiezaFilamentLine.objects.create(
            pieza=pieza, filament=self.fil, grams_used=Decimal("50")
        )
        pres = self._pres(3)
        pres.approve()
        job = pres.jobs.get(pieza=pieza)
        self.assertEqual(job.quantity, 3)
        self.assertEqual(job.gcode_runs, 1)
        self.assertEqual(job.surplus_units, 1)
        # Al imprimirse, la sobrante (1) se suma al stock de la pieza.
        added = job.register_surplus()
        self.assertEqual(added, 1)
        pieza.refresh_from_db()
        self.assertEqual(pieza.stock_quantity, 1)
        # Idempotente: no vuelve a sumar.
        self.assertEqual(job.register_surplus(), 0)
        pieza.refresh_from_db()
        self.assertEqual(pieza.stock_quantity, 1)

    def test_pieza_ams_va_a_maquina_con_ams(self):
        ams = Maquina.objects.create(name="Bambu", is_active=True, supports_multicolor=True)
        self.pieza.requires_ams = True
        self.pieza.save()
        pres = self._pres(1)
        pres.approve()
        job = pres.jobs.first()
        self.assertEqual(job.machine, ams)
        self.assertTrue(job.requires_multicolor)


class CancelacionReversaTests(TestCase):
    """Al cancelar un pedido aprobado se devuelve el inventario descontado."""

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
        self.agg = Aggregate.objects.create(
            name="Argolla", cost_per_unit=Decimal("50"), stock_quantity=Decimal("10")
        )
        self.maquina = Maquina.objects.create(name="Ender", is_active=True)
        self.p = make_producto()
        self.pieza = add_pieza(self.p, self.fil, Decimal("100"))  # 1u, ppg 1, 100g
        ProductoAggregateLine.objects.create(
            producto=self.p, aggregate=self.agg, quantity=Decimal("1")
        )

    def _pres(self, quantity):
        pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(
            presupuesto=pres, producto=self.p, quantity=quantity
        )
        return pres

    def test_cancelar_devuelve_filamento_y_agregados(self):
        pres = self._pres(2)
        pres.approve()
        self.fil.refresh_from_db()
        self.agg.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("9800.00"))  # -200 g
        self.assertEqual(self.agg.stock_quantity, Decimal("8.00"))  # -2

        result = pres.cancel()

        pres.refresh_from_db()
        self.fil.refresh_from_db()
        self.agg.refresh_from_db()
        self.assertEqual(pres.status, Presupuesto.Status.CANCELLED)
        self.assertTrue(pres.stock_reversed)
        self.assertEqual(self.fil.stock_grams, Decimal("10000.00"))  # devuelto
        self.assertEqual(self.agg.stock_quantity, Decimal("10.00"))  # devuelto
        self.assertEqual(
            pres.jobs.filter(status=self.PJ.Status.CANCELLED).count(), 1
        )
        self.assertEqual(result["jobs_cancelled"], 1)

    def test_cancelar_es_idempotente(self):
        pres = self._pres(2)
        pres.approve()
        pres.cancel()
        pres.cancel()  # segunda vez no debe volver a sumar
        self.fil.refresh_from_db()
        self.agg.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("10000.00"))
        self.assertEqual(self.agg.stock_quantity, Decimal("10.00"))

    def test_cancelar_devuelve_piezas_tomadas_de_stock(self):
        self.pieza.stock_quantity = 5
        self.pieza.save()
        pres = self._pres(3)  # 3 salen de stock, no se imprime nada
        pres.approve()
        self.pieza.refresh_from_db()
        self.assertEqual(self.pieza.stock_quantity, 2)

        pres.cancel()

        self.pieza.refresh_from_db()
        self.assertEqual(self.pieza.stock_quantity, 5)  # devueltas

    def test_cancelar_con_trabajo_impreso_pasa_piezas_a_stock(self):
        pres = self._pres(2)
        pres.approve()
        job = pres.jobs.first()
        job.status = self.PJ.Status.DONE
        job.save(update_fields=["status"])

        pres.cancel()

        self.pieza.refresh_from_db()
        self.fil.refresh_from_db()
        # Las 2 piezas impresas pasan al stock; el filamento NO se devuelve
        # (ya se usó de verdad al imprimir).
        self.assertEqual(self.pieza.stock_quantity, 2)
        self.assertEqual(self.fil.stock_grams, Decimal("9800.00"))

    def test_forecast_marca_filamento_en_negativo(self):
        from production.scheduler import material_forecast

        self.fil.stock_grams = Decimal("100")
        self.fil.min_stock = Decimal("1000")
        self.fil.save()
        pres = self._pres(2)  # consume 200 g; 100 - 200 = -100
        pres.approve()
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("-100.00"))

        forecast = material_forecast()
        items = {r["item"]: r for r in forecast["filaments"]}
        self.assertIn(str(self.fil), items)
        row = items[str(self.fil)]
        self.assertEqual(row["stock"], Decimal("-100.00"))
        self.assertEqual(row["shortfall"], Decimal("1100.00"))  # 1000 - (-100)


class PriorityTests(TestCase):
    """La prioridad manual del producto define el orden en la cola."""

    def setUp(self):
        from production.models import ProductionJob

        self.PJ = ProductionJob
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("100000"),
        )
        # Una sola máquina para que todo caiga en la misma cola.
        self.maquina = Maquina.objects.create(name="Ender", is_active=True)

    def _producto(self, name, priority, horas=Decimal("2")):
        p = make_producto(name=name, priority=priority)
        add_pieza(p, self.fil, Decimal("100"), print_hours=horas)
        return p

    def test_alta_se_encola_antes_que_sin_prioridad(self):
        baja = self._producto("Sin prio", Producto.Priority.SIN, horas=Decimal("1"))
        alta = self._producto("Urgente", Producto.Priority.ALTA, horas=Decimal("5"))
        pres = Presupuesto.objects.create(client_name="Cliente")
        # Cargamos primero el de menor prioridad para asegurar que no es por orden de carga.
        PresupuestoItem.objects.create(presupuesto=pres, producto=baja, quantity=1)
        PresupuestoItem.objects.create(presupuesto=pres, producto=alta, quantity=1)
        pres.approve()

        jobs = list(
            self.PJ.objects.filter(machine=self.maquina).order_by(
                "producto__priority", "order", "id"
            )
        )
        self.assertEqual(jobs[0].producto, alta)
        self.assertEqual(jobs[1].producto, baja)
        # El job de alta prioridad se asignó con order menor (entra antes).
        self.assertLess(
            pres.jobs.get(producto=alta).order,
            pres.jobs.get(producto=baja).order,
        )

    def test_default_es_sin_prioridad(self):
        p = make_producto(name="Default")
        self.assertEqual(p.priority, Producto.Priority.SIN)


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
        add_pieza(self.p, self.fil, Decimal("100"))
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


class StockProductosTerminadosTests(TestCase):
    """Un pedido 'para stock' suma sus productos terminados al completarse."""

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
        self.producto = make_producto()
        add_pieza(self.producto, self.fil, Decimal("100"))

    def _pedido_para_stock(self, cantidad):
        pres = Presupuesto.objects.create(client_name="", para_stock=True)
        PresupuestoItem.objects.create(
            presupuesto=pres, producto=self.producto, quantity=cantidad
        )
        pres.approve()
        pres.refresh_from_db()
        return pres

    def test_pedido_de_stock_completado_suma_al_stock_de_terminados(self):
        pres = self._pedido_para_stock(3)
        for job in pres.jobs.all():
            job.status = self.PJ.Status.DONE
            job.save()
        pres.sync_status_from_jobs()
        pres.refresh_from_db()
        self.producto.refresh_from_db()
        self.assertEqual(pres.status, Presupuesto.Status.COMPLETED)
        self.assertEqual(self.producto.stock_quantity, 3)
        self.assertTrue(pres.finished_stock_added)

    def test_alta_al_stock_es_idempotente(self):
        pres = self._pedido_para_stock(2)
        for job in pres.jobs.all():
            job.status = self.PJ.Status.DONE
            job.save()
        pres.sync_status_from_jobs()
        # Volver a llamar no debe duplicar el alta.
        pres.add_finished_to_stock()
        pres.add_finished_to_stock()
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_quantity, 2)

    def test_pedido_de_cliente_no_suma_al_stock(self):
        pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(
            presupuesto=pres, producto=self.producto, quantity=2
        )
        pres.approve()
        for job in pres.jobs.all():
            job.status = self.PJ.Status.DONE
            job.save()
        pres.sync_status_from_jobs()
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_quantity, 0)

    def test_no_suma_antes_de_completar(self):
        pres = self._pedido_para_stock(2)
        # Aprobado pero no completado: todavía no suma.
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_quantity, 0)
        self.assertFalse(pres.finished_stock_added)

    def test_is_low_stock(self):
        self.producto.min_stock = 5
        self.producto.stock_quantity = 2
        self.assertTrue(self.producto.is_low_stock)
        self.assertEqual(self.producto.stock_to_make, 3)
        self.producto.stock_quantity = 5
        self.assertFalse(self.producto.is_low_stock)
        self.assertEqual(self.producto.stock_to_make, 0)


class ConsumoStockTerminadosTests(TestCase):
    """Un pedido de cliente sirve primero del stock de productos terminados."""

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
        self.agg = Aggregate.objects.create(
            name="Argolla", cost_per_unit=Decimal("50"), stock_quantity=Decimal("10")
        )
        self.maquina = Maquina.objects.create(name="Ender", is_active=True)
        self.p = make_producto()
        self.pieza = add_pieza(self.p, self.fil, Decimal("100"))  # 1u, ppg1, 100g
        ProductoAggregateLine.objects.create(
            producto=self.p, aggregate=self.agg, quantity=Decimal("1")
        )

    def _pres(self, quantity, para_stock=False):
        pres = Presupuesto.objects.create(
            client_name="" if para_stock else "Cliente", para_stock=para_stock
        )
        PresupuestoItem.objects.create(presupuesto=pres, producto=self.p, quantity=quantity)
        return pres

    def test_stock_suficiente_no_produce_nada(self):
        self.p.stock_quantity = 5
        self.p.save()
        pres = self._pres(3)
        result = pres.approve()
        self.p.refresh_from_db()
        self.fil.refresh_from_db()
        self.agg.refresh_from_db()
        # Sirvió 3 del stock de terminados: no hay trabajos, ni consumo.
        self.assertEqual(self.p.stock_quantity, 2)
        self.assertEqual(pres.jobs.count(), 0)
        self.assertEqual(self.fil.stock_grams, Decimal("10000.00"))
        self.assertEqual(self.agg.stock_quantity, Decimal("10.00"))
        self.assertEqual(result["from_finished"][0]["units"], 3)

    def test_stock_parcial_produce_el_resto(self):
        self.p.stock_quantity = 2
        self.p.save()
        pres = self._pres(5)  # 2 de stock, 3 a producir
        pres.approve()
        self.p.refresh_from_db()
        self.fil.refresh_from_db()
        self.agg.refresh_from_db()
        self.assertEqual(self.p.stock_quantity, 0)
        # Produce 3: 3 trabajos (1 pieza c/u), 300 g de filamento, 3 agregados.
        total_qty = sum(j.quantity for j in pres.jobs.all())
        self.assertEqual(total_qty, 3)
        self.assertEqual(self.fil.stock_grams, Decimal("9700.00"))  # -300
        self.assertEqual(self.agg.stock_quantity, Decimal("7.00"))  # -3
        item = pres.items.first()
        self.assertEqual(item.from_finished_stock, 2)

    def test_pedido_para_stock_no_consume_su_stock(self):
        self.p.stock_quantity = 10
        self.p.save()
        pres = self._pres(2, para_stock=True)
        pres.approve()
        self.p.refresh_from_db()
        # No tocó el stock de terminados; produjo las 2.
        self.assertEqual(self.p.stock_quantity, 10)
        self.assertEqual(sum(j.quantity for j in pres.jobs.all()), 2)

    def test_cancelar_devuelve_stock_terminados_y_lo_producido(self):
        self.p.stock_quantity = 2
        self.p.save()
        pres = self._pres(5)  # 2 de stock + 3 producidas
        pres.approve()
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock_quantity, 0)
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("9700.00"))

        result = pres.cancel()

        self.p.refresh_from_db()
        self.fil.refresh_from_db()
        self.agg.refresh_from_db()
        # Vuelven las 2 de stock de terminados y el material de las 3 producidas.
        self.assertEqual(self.p.stock_quantity, 2)
        self.assertEqual(self.fil.stock_grams, Decimal("10000.00"))
        self.assertEqual(self.agg.stock_quantity, Decimal("10.00"))
        self.assertEqual(result["finished"][0]["units"], 2)
        pres.items.first().refresh_from_db()
        self.assertEqual(pres.items.first().from_finished_stock, 0)

    def test_cancelar_solo_stock_terminados_sin_produccion(self):
        self.p.stock_quantity = 5
        self.p.save()
        pres = self._pres(3)  # todo de stock
        pres.approve()
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock_quantity, 2)

        pres.cancel()

        self.p.refresh_from_db()
        self.assertEqual(self.p.stock_quantity, 5)  # devueltas las 3

    def test_cancelar_es_idempotente_con_stock_terminados(self):
        self.p.stock_quantity = 4
        self.p.save()
        pres = self._pres(2)
        pres.approve()
        pres.cancel()
        pres.cancel()
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock_quantity, 4)


class ListoParaEntregarTests(TestCase):
    """Pedido de cliente servido 100% del stock: listo para entregar, sin producción."""

    def setUp(self):
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("10000"),
        )
        self.p = make_producto()
        add_pieza(self.p, self.fil, Decimal("100"))

    def _pres(self, quantity, para_stock=False):
        pres = Presupuesto.objects.create(
            client_name="" if para_stock else "Cliente", para_stock=para_stock
        )
        PresupuestoItem.objects.create(
            presupuesto=pres, producto=self.p, quantity=quantity
        )
        return pres

    def test_servido_entero_queda_listo_para_entregar(self):
        self.p.stock_quantity = 5
        self.p.save()
        pres = self._pres(3)
        pres.approve()
        self.assertEqual(pres.jobs.count(), 0)
        self.assertTrue(pres.is_ready_to_deliver)

    def test_con_produccion_no_esta_listo(self):
        self.p.stock_quantity = 1
        self.p.save()
        pres = self._pres(3)  # 1 de stock, 2 a producir
        pres.approve()
        self.assertTrue(pres.jobs.exists())
        self.assertFalse(pres.is_ready_to_deliver)

    def test_sin_aprobar_no_esta_listo(self):
        self.p.stock_quantity = 5
        self.p.save()
        pres = self._pres(3)
        self.assertFalse(pres.is_ready_to_deliver)

    def test_para_stock_no_esta_listo_para_entregar(self):
        # Un pedido de reposición de stock no se entrega: su terminado va al
        # stock interno al completarse, así que nunca es "listo para entregar".
        self.p.stock_quantity = 5
        self.p.save()
        pres = self._pres(3, para_stock=True)
        pres.approve()
        # para_stock siempre produce (no consume su propio stock), pero aunque no
        # generara jobs, no debe marcarse como listo para entregar.
        self.assertFalse(pres.is_ready_to_deliver)


class MarcarEntregadoAdminTests(TestCase):
    """La acción del admin completa un pedido listo de stock (sin producción)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        self.reverse = reverse
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        self.fil = Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("10000"),
        )
        self.p = make_producto(stock_quantity=5)
        add_pieza(self.p, self.fil, Decimal("100"))
        self.pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(
            presupuesto=self.pres, producto=self.p, quantity=3
        )
        self.pres.approve()

    def test_accion_marca_entregado_y_completa(self):
        url = self.reverse("admin:budgets_presupuesto_changelist")
        self.client.post(
            url,
            {
                "action": "marcar_entregado",
                "_selected_action": [self.pres.pk],
            },
        )
        self.pres.refresh_from_db()
        self.assertEqual(self.pres.status, Presupuesto.Status.COMPLETED)
        self.assertIsNotNone(self.pres.completed_at)
