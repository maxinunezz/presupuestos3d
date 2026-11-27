"""«Deshacer» de Roque (27/11/2026): cada escritura se revierte entera dentro de los 15 minutos."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from caja.models import CuentaCaja, MovimientoCaja
from gastos.models import CategoriaGasto, Gasto, MedioPagoGasto
from inventory.models import Compra, Filament
from production.models import ProductionJob

from .models import PagoPresupuesto, Presupuesto, RegistroAgente
from .tests_circuito import _Base

ESCRITURA = "token-escritura"


@override_settings(AGENTES_API_TOKEN="token-lectura", AGENTES_ESCRITURA_TOKEN=ESCRITURA)
class DeshacerTests(_Base):
    def setUp(self):
        super().setUp()
        self.c = APIClient()
        self.c.credentials(HTTP_X_AGENTES_ESCRITURA_TOKEN=ESCRITURA)
        self.banco = CuentaCaja.objects.create(nombre="Banco", saldo_inicial=Decimal("0"), medio_pago_default="TRANSFERENCIA",
                                               fecha_saldo_inicial=date(2026, 1, 1))
        CategoriaGasto.objects.get_or_create(nombre="Administración")
        MedioPagoGasto.objects.get_or_create(nombre="Transferencia")

    def post(self, ruta, **d):
        return self.c.post(f"/api/agentes/escritura/{ruta}", {"aprobo": "Lucas", **d}, format="json")

    def deshacer(self, clave_original, clave="d1"):
        return self.post("deshacer/", clave=clave, clave_original=clave_original)

    def test_sena_y_aprobacion_se_deshacen_enteras(self):
        p = self._pedido()
        r = self.post(f"presupuestos/{p.pk}/pago/", clave="pago1", tipo="SENA", monto="1000", medio="TRANSFERENCIA")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["caja"], "Banco")
        mov = PagoPresupuesto.objects.get().movimiento_caja
        self.assertEqual(mov.estado, MovimientoCaja.Estado.CONFIRMADO)  # caja uniforme: entra confirmado
        self.assertEqual(self.post(f"presupuestos/{p.pk}/aprobar/", clave="ap1").status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.APPROVED)
        self.assertLess(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("10000"))
        # deshacer en orden inverso: aprobación y después la seña
        r = self.deshacer("ap1")
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.DRAFT)
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).stock_grams, Decimal("10000"))
        self.assertFalse(p.jobs.exclude(status=ProductionJob.Status.CANCELLED).exists())
        self.assertEqual(self.deshacer("pago1", "d2").status_code, 200)
        self.assertFalse(PagoPresupuesto.objects.exists())
        self.assertEqual(CuentaCaja.objects.get(pk=self.banco.pk).saldo_actual, Decimal("0.00"))
        # y se puede volver a aprobar normalmente
        self._sena(p, Decimal("1000"))
        p.refresh_from_db()
        p.approve()
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.APPROVED)

    def test_no_se_deshace_si_ya_se_imprimio(self):
        p = self._pedido()
        self._sena(p, Decimal("1000"))
        self.post(f"presupuestos/{p.pk}/aprobar/", clave="ap2")
        j = p.jobs.first()
        j.status = ProductionJob.Status.PRINTING
        j.save()
        r = self.deshacer("ap2")
        self.assertEqual(r.status_code, 409)
        self.assertIn("imprimir", r.json()["error"])

    def test_compra_se_deshace_stock_precio_y_caja(self):
        r = self.post("compras/", clave="c1", proveedor="TP3D", medio_pago="Transferencia",
                      lineas=[{"filamento_id": self.fil.id, "cantidad": "2000", "precio_total": "30000"}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Filament.objects.get(pk=self.fil.pk).cost_per_kg, Decimal("15000"))
        self.assertEqual(self.deshacer("c1").status_code, 200)
        f = Filament.objects.get(pk=self.fil.pk)
        self.assertEqual((f.stock_grams, f.cost_per_kg), (Decimal("10000.00"), Decimal("10000.00")))
        self.assertFalse(Compra.objects.exists())
        self.assertEqual(CuentaCaja.objects.get(pk=self.banco.pk).saldo_actual, Decimal("0.00"))

    def test_gasto_se_deshace_con_su_caja(self):
        self.post("gastos/", clave="g1", concepto="Uber", monto="15000", categoria="Administración",
                  medio_pago="Transferencia")
        self.assertEqual(CuentaCaja.objects.get(pk=self.banco.pk).saldo_actual, Decimal("-15000.00"))
        self.assertEqual(self.deshacer("g1").status_code, 200)
        self.assertFalse(Gasto.objects.exists())
        self.assertEqual(CuentaCaja.objects.get(pk=self.banco.pk).saldo_actual, Decimal("0.00"))

    def test_una_sola_vez_y_dentro_del_plazo(self):
        self.post("gastos/", clave="g2", concepto="X", monto="10", categoria="Administración")
        self.assertEqual(self.deshacer("g2").status_code, 200)
        self.assertEqual(self.deshacer("g2", "d3").status_code, 409)
        self.post("gastos/", clave="g3", concepto="Y", monto="10", categoria="Administración")
        RegistroAgente.objects.filter(clave="g3").update(creado=timezone.now() - timedelta(minutes=20))
        r = self.deshacer("g3", "d4")
        self.assertEqual(r.status_code, 409)
        self.assertIn("15 minutos", r.json()["error"])

    def test_post_impresion_y_entrega_vuelven_atras(self):
        p = self._pedido()
        self._sena(p, Decimal("1000"))
        p.approve()
        self._imprimir_todo(p)
        self.post(f"presupuestos/{p.pk}/post-impresion/", clave="pi1")
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.READY)
        self.assertEqual(self.deshacer("pi1").status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.status, Presupuesto.Status.POST_PRINT)

    def test_lo_que_no_se_deshace_lo_dice(self):
        p = self._pedido()
        RegistroAgente.objects.create(operacion="editar", clave="e1", aprobo="Lucas", presupuesto=p, pedido={}, resultado={})
        r = self.deshacer("e1")
        self.assertEqual(r.status_code, 409)
