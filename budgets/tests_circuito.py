"""Circuito venta → diseño → producción → entrega (decisiones del 27/11/2026)."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from caja.models import CuentaCaja, MovimientoCaja
from inventory.models import Filament
from production.models import Maquina, ProductionJob, Prototipo, PrototipoFilamento

from .models import Cliente, PagoPresupuesto, Pieza, Presupuesto, PresupuestoItem, Producto, SenaFaltanteError
from .tests import add_pieza, make_producto

ESCRITURA = "token-escritura"


class _Base(TestCase):
    def setUp(self):
        Maquina.objects.create(name="Bambu", is_active=True, supports_multicolor=True)
        self.fil = Filament.objects.create(brand="G", material_type="PLA", color="Rojo",
                                           cost_per_kg=Decimal("10000"), stock_grams=Decimal("10000"))
        self.prod = make_producto(name="Cortante", sale_price=Decimal("1000"))
        self.pieza = add_pieza(self.prod, self.fil, Decimal("100"))

    def _pedido(self, cantidad=2, producto=None, cliente="Panadería Sol"):
        p = Presupuesto.objects.create(client_name=cliente)
        PresupuestoItem.objects.create(presupuesto=p, producto=producto or self.prod, quantity=cantidad)
        return p

    def _sena(self, p, monto=None, medio="TRANSFERENCIA"):
        return PagoPresupuesto.objects.create(presupuesto=p, tipo="SENA", monto=monto or p.total / 2, medio_pago=medio)

    def _imprimir_todo(self, p):
        for j in p.jobs.all():
            j.status = ProductionJob.Status.DONE
            j.save()
        p.sync_status_from_jobs()
        p.refresh_from_db()


class SenaTests(_Base):
    def test_sin_sena_no_se_confirma(self):
        p = self._pedido()
        with self.assertRaises(SenaFaltanteError):
            p.approve()
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.DRAFT)
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("10000"))

    def test_con_la_mitad_se_confirma(self):
        p = self._pedido()
        self._sena(p, Decimal("1000"))
        p.approve()
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.APPROVED)
        self.assertEqual(p.estado_venta, Presupuesto.EstadoVenta.PARCIAL)
        self.assertEqual(p.saldo, Decimal("1000.00"))

    def test_pedido_viejo_marcado_a_mano_pasa(self):
        p = self._pedido()
        p.estado_venta = Presupuesto.EstadoVenta.PARCIAL
        p.save()
        p.approve()

    def test_para_stock_no_pide_sena(self):
        p = Presupuesto.objects.create(client_name="", para_stock=True)
        PresupuestoItem.objects.create(presupuesto=p, producto=self.prod, quantity=1)
        p.approve()

    @override_settings(SENA_MINIMA_PCT=0)
    def test_configurable(self):
        self._pedido().approve()


class PagosTests(_Base):
    def setUp(self):
        super().setUp()
        self.cuenta = CuentaCaja.objects.create(nombre="Banco", saldo_inicial=Decimal("0"),
                                                fecha_saldo_inicial=timezone.localdate(),
                                                medio_pago_default="TRANSFERENCIA")

    def test_cada_pago_va_a_caja_y_el_saldo_sale_solo(self):
        p = self._pedido()
        self._sena(p, Decimal("1000"))
        PagoPresupuesto.objects.create(presupuesto=p, tipo="SALDO", monto=Decimal("1000"), medio_pago="TRANSFERENCIA")
        p.refresh_from_db()
        self.assertEqual(p.estado_venta, Presupuesto.EstadoVenta.PAGADO)
        self.assertIsNotNone(p.fecha_cobro)
        self.assertEqual(MovimientoCaja.objects.filter(presupuesto=p, tipo="INGRESO").count(), 2)

    def test_tarjeta_no_genera_caja(self):
        p = self._pedido()
        self._sena(p, Decimal("1000"), medio="TARJETA")
        self.assertFalse(MovimientoCaja.objects.filter(presupuesto=p).exists())

    def test_borrar_un_pago_descarta_su_borrador(self):
        p = self._pedido()
        pago = self._sena(p, Decimal("1000"))
        mov = pago.movimiento_caja
        pago.delete()
        mov.refresh_from_db()
        p.refresh_from_db()
        self.assertEqual(mov.estado, MovimientoCaja.Estado.DESCARTADO)
        self.assertEqual(p.estado_venta, Presupuesto.EstadoVenta.PARCIAL)  # sin pagos: queda como estaba

    def test_cancelar_no_devuelve_la_sena_sola(self):
        p = self._pedido()
        pago = self._sena(p, Decimal("1000"))
        MovimientoCaja.objects.filter(pk=pago.movimiento_caja_id).update(estado=MovimientoCaja.Estado.CONFIRMADO)
        p.approve()
        p.cancel()
        self.assertFalse(MovimientoCaja.objects.filter(presupuesto=p, tipo="EGRESO").exists())

    def test_envio_a_cargo_del_cliente_suma_al_saldo(self):
        p = self._pedido()
        p.forma_entrega, p.costo_envio, p.envio_cobrado_al_cliente = "MOTO", Decimal("400"), True
        p.save()
        self.assertEqual(p.total_a_cobrar, Decimal("2400.00"))


class EntregaTests(_Base):
    def test_impreso_post_impresion_listo_entregado(self):
        p = self._pedido()
        self._sena(p)
        p.approve()
        self._imprimir_todo(p)
        self.assertEqual(p.status, Presupuesto.Status.POST_PRINT)
        self.assertFalse(p.is_ready_to_deliver)
        self.assertTrue(p.terminar_post_impresion())
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.READY)
        self.assertIsNotNone(p.ready_at)
        self.assertTrue(p.marcar_entregado("Lucas"))
        p.refresh_from_db()
        self.assertEqual((p.status, p.delivered_by), (Presupuesto.Status.COMPLETED, "Lucas"))
        self.assertIsNotNone(p.completed_at)

    def test_no_se_entrega_sin_terminar_post_impresion(self):
        p = self._pedido()
        self._sena(p)
        p.approve()
        self._imprimir_todo(p)
        self.assertFalse(p.marcar_entregado("Lucas"))


class DisenoPendienteTests(_Base):
    def setUp(self):
        super().setUp()
        self.nuevo = make_producto(name="Vaso chancho", sale_price=Decimal("500"), diseno_listo=False)
        add_pieza(self.nuevo, self.fil, Decimal("50"), name="Vaso")

    def test_nuevo_queda_en_diseno_con_precio_provisorio(self):
        self.assertEqual(self.nuevo.estado_diseno, Producto.EstadoDiseno.EN_DISENO)
        p = self._pedido(producto=self.nuevo)
        self.assertTrue(p.items.get().precio_provisorio)

    def test_se_confirma_y_la_linea_espera_hasta_diseno_listo(self):
        p = self._pedido(cantidad=2, producto=self.nuevo)
        PresupuestoItem.objects.create(presupuesto=p, producto=self.prod, quantity=1)
        self._sena(p, Decimal("1000"))
        r = p.approve()
        self.assertEqual(r["esperando_diseno"], ["Vaso chancho"])
        self.assertEqual(p.jobs.filter(producto=self.nuevo).count(), 0)
        self.assertEqual(p.jobs.filter(producto=self.prod).count(), 1)
        self.assertTrue(p.tiene_items_esperando_diseno)
        # lo impreso no lo pasa a post-impresión mientras haya líneas esperando
        self._imprimir_todo(p)
        self.assertNotEqual(p.status, Presupuesto.Status.POST_PRINT)
        # Lucas cierra el diseño con el precio final
        self.nuevo.sale_price = Decimal("800")
        self.nuevo.estado_diseno = Producto.EstadoDiseno.LISTO
        self.nuevo.save()
        p.refresh_from_db()
        item = p.items.get(producto=self.nuevo)
        self.assertTrue(item.produccion_generada)
        self.assertFalse(item.precio_provisorio)
        self.assertEqual(item.unit_price, Decimal("800.00"))
        self.assertEqual(p.jobs.filter(producto=self.nuevo).count(), 1)
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("10000") - 100 - 100)

    def test_rondas_con_el_cliente(self):
        for _ in range(2):
            self.nuevo.estado_diseno = Producto.EstadoDiseno.ESPERANDO_CLIENTE
            self.nuevo.save()
            self.nuevo.estado_diseno = Producto.EstadoDiseno.EN_DISENO
            self.nuevo.save()
        self.nuevo.refresh_from_db()
        self.assertEqual(self.nuevo.rondas_cliente, 2)
        self.assertTrue(self.nuevo.cobra_rediseno)
        self.assertFalse(self.nuevo.diseno_listo)


class PrioridadTests(_Base):
    def test_la_prioridad_es_del_cliente(self):
        p = self._pedido(cliente="AVEIT")
        self.assertEqual(p.prioridad, Producto.Priority.SIN)
        c = Cliente.objects.get(nombre__iexact="aveit")
        c.prioridad = Producto.Priority.ALTA
        c.save()
        p.refresh_from_db()
        self.assertEqual(p.prioridad, Producto.Priority.ALTA)
        otro = self._pedido(cliente="aveit")  # mismo cliente, otra forma de escribirlo
        self.assertEqual(otro.prioridad, Producto.Priority.ALTA)
        self.assertEqual(Cliente.objects.filter(nombre__iexact="aveit").count(), 1)


class EditarPedidoTests(_Base):
    def _confirmado(self, cantidad):
        p = self._pedido(cantidad=cantidad)
        self._sena(p, p.total)
        p.approve()
        p.refresh_from_db()
        return p

    def test_subir_la_cantidad_encola_y_descuenta_la_diferencia(self):
        p = self._confirmado(2)
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("9800"))
        antes = p.foto_items()
        PresupuestoItem.objects.filter(presupuesto=p).update(quantity=5)
        p.recalcular_pedido(antes)
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("9500"))
        abiertos = p.jobs.exclude(status=ProductionJob.Status.CANCELLED)
        self.assertEqual(sum(j.quantity for j in abiertos), 5)

    def test_bajar_la_cantidad_con_lo_impreso_va_a_stock_de_piezas(self):
        p = self._confirmado(4)
        self._imprimir_todo(p)  # 4 impresas → post-impresión
        antes = p.foto_items()
        PresupuestoItem.objects.filter(presupuesto=p).update(quantity=1)
        p.recalcular_pedido(antes)
        p.refresh_from_db()
        self.assertEqual(Pieza.objects.get(pk=self.pieza.pk).stock_quantity, 3)
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("9600"))  # lo impreso no vuelve
        self.assertEqual(p.status, Presupuesto.Status.POST_PRINT)

    def test_subir_despues_de_imprimir_vuelve_a_produccion(self):
        p = self._confirmado(1)
        self._imprimir_todo(p)
        antes = p.foto_items()
        PresupuestoItem.objects.filter(presupuesto=p).update(quantity=3)
        p.recalcular_pedido(antes)
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.IN_PRODUCTION)
        pendientes = p.jobs.filter(status=ProductionJob.Status.PENDING)
        self.assertEqual(sum(j.quantity for j in pendientes), 2)


class VencimientoTests(_Base):
    def test_enviado_hace_mas_de_10_dias_se_cancela(self):
        viejo, nuevo = self._pedido(cliente="Viejo"), self._pedido(cliente="Nuevo")
        Presupuesto.objects.filter(pk=viejo.pk).update(status="SENT", sent_at=timezone.now() - timedelta(days=11))
        Presupuesto.objects.filter(pk=nuevo.pk).update(status="SENT", sent_at=timezone.now() - timedelta(days=3))
        self.assertEqual([v["id"] for v in Presupuesto.vencer_sin_respuesta(simular=True)], [viejo.pk])
        self.assertEqual(Presupuesto.objects.get(pk=viejo.pk).status, "SENT")  # simular no toca nada
        Presupuesto.vencer_sin_respuesta()
        self.assertEqual(Presupuesto.objects.get(pk=viejo.pk).status, "CANCELLED")
        self.assertEqual(Presupuesto.objects.get(pk=nuevo.pk).status, "SENT")


class PrototipoTests(_Base):
    def test_descuenta_corrige_y_devuelve(self):
        proto = Prototipo.objects.create(descripcion="Prueba vaso")
        linea = PrototipoFilamento.objects.create(prototipo=proto, filament=self.fil, gramos=Decimal("45"))
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("9955"))
        linea.gramos = Decimal("60")
        linea.save()
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("9940"))
        proto.delete()  # en cascada: devuelve
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("10000"))


@override_settings(AGENTES_ESCRITURA_TOKEN=ESCRITURA)
class ApiCircuitoTests(_Base):
    def setUp(self):
        super().setUp()
        self.c = APIClient()
        self.c.credentials(HTTP_X_AGENTES_ESCRITURA_TOKEN=ESCRITURA)

    def _post(self, ruta, **datos):
        return self.c.post(f"/api/agentes/escritura/{ruta}/", {"aprobo": "Lucas", **datos}, format="json")

    def test_pago_y_confirmar(self):
        p = self._pedido()
        r = self._post(f"presupuestos/{p.pk}/aprobar", clave="a1")
        self.assertEqual((r.status_code, r.json()["motivo"]), (409, "SenaFaltanteError"))
        r = self._post(f"presupuestos/{p.pk}/pago", clave="p1", tipo="SENA", monto="1.000", medio="Mercado Pago")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["saldo"], "1000.00")
        self.assertEqual(self._post(f"presupuestos/{p.pk}/aprobar", clave="a2").status_code, 200)

    def test_diseno_listo_solo_lucas(self):
        nuevo = make_producto(name="Nuevo", diseno_listo=False)
        r = self.c.post(f"/api/agentes/escritura/productos/{nuevo.pk}/etapa-diseno/",
                        {"clave": "d1", "aprobo": "Dana", "etapa": "LISTO"}, format="json")
        self.assertEqual(r.status_code, 403)
        r = self._post(f"productos/{nuevo.pk}/etapa-diseno", clave="d2", etapa="ESPERANDO_CLIENTE")
        self.assertEqual(r.json()["rondas_cliente"], 1)
        r = self._post(f"productos/{nuevo.pk}/etapa-diseno", clave="d3", etapa="LISTO")
        self.assertEqual(r.json()["etapa"], "Diseño listo")

    def test_editar_prioridad_prototipo_y_envio(self):
        p = self._pedido(cantidad=2)
        self._sena(p, p.total)
        p.approve()
        r = self._post(f"presupuestos/{p.pk}/editar", clave="e1", items=[{"producto_id": self.prod.pk, "cantidad": 3}])
        self.assertTrue(r.json()["recalculado"], r.content)
        self.assertEqual(p.items.get().quantity, 3)
        r = self._post("clientes/prioridad", clave="pr1", cliente="panaderia sol", prioridad=1)
        self.assertEqual(r.status_code, 200, r.content)
        r = self._post("prototipos", clave="pt1", descripcion="Prueba", filamentos=[{"filament_id": self.fil.pk, "gramos": 20}])
        self.assertEqual(r.status_code, 201, r.content)
        r = self._post(f"presupuestos/{p.pk}/envio", clave="en1", forma="moto", costo="400", a_cargo_cliente=True)
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual(p.forma_entrega, "MOTO")
