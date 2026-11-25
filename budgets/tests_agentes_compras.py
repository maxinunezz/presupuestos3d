"""Compras y gastos de Roque: la compra de insumos suma stock y actualiza precio; lo demás queda como gasto."""
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from caja.models import CuentaCaja, MovimientoCaja
from gastos.models import CategoriaGasto, Gasto, MedioPagoGasto
from inventory.models import Aggregate, Compra, Filament, StockMovement

from .models import RegistroAgente

ESCRITURA = "token-escritura"


@override_settings(AGENTES_API_TOKEN="token-lectura", AGENTES_ESCRITURA_TOKEN=ESCRITURA)
class ComprasGastosAgentesTests(TestCase):
    def setUp(self):
        self.c = APIClient()
        self.c.credentials(HTTP_X_AGENTES_ESCRITURA_TOKEN=ESCRITURA)
        self.negro = Filament.objects.create(brand="Grilon", material_type="PLA", color="Negro",
                                             cost_per_kg=Decimal("15000"), stock_grams=Decimal("500"))
        self.bolsa = Aggregate.objects.create(name="Bolsa", cost_per_unit=Decimal("20"), stock_quantity=Decimal("30"))
        CategoriaGasto.objects.get_or_create(nombre="Administración")
        MedioPagoGasto.objects.get_or_create(nombre="Transferencia")
        CuentaCaja.objects.filter(medio_pago_default="TRANSFERENCIA").delete()
        CuentaCaja.objects.update_or_create(nombre="Banco", defaults={"medio_pago_default": "TRANSFERENCIA", "fecha_saldo_inicial": date(2026, 1, 1)})

    def _compra(self, clave="c1", **extra):
        return self.c.post("/api/agentes/escritura/compras/", {
            "clave": clave, "aprobo": "Lucas", "proveedor": "Grilon", "medio_pago": "Transferencia",
            "lineas": [{"filamento_id": self.negro.id, "cantidad": "2000", "precio_total": "32000"},
                       {"insumo_id": self.bolsa.id, "cantidad": "100", "precio_total": "2500"}], **extra}, format="json")

    def test_compra_suma_stock_actualiza_precio_y_deja_borrador_en_caja(self):
        r = self._compra()
        self.assertEqual(r.status_code, 201, r.content)
        self.negro.refresh_from_db()
        self.bolsa.refresh_from_db()
        self.assertEqual((self.negro.stock_grams, self.negro.cost_per_kg), (Decimal("2500"), Decimal("16000")))
        self.assertEqual((self.bolsa.stock_quantity, self.bolsa.cost_per_unit), (Decimal("130"), Decimal("25")))
        self.assertEqual(Compra.objects.get().status, Compra.Status.CONFIRMED)
        self.assertEqual(StockMovement.objects.filter(reason=StockMovement.Reason.PURCHASE).count(), 2)
        mov = MovimientoCaja.objects.get()
        self.assertEqual((mov.estado, mov.monto, mov.tipo), (MovimientoCaja.Estado.BORRADOR, Decimal("34500"),
                                                             MovimientoCaja.Tipo.EGRESO))
        d = r.json()
        self.assertEqual((d["lineas"][0]["stock_antes"], d["lineas"][0]["stock_despues"]), ("500.00", "2500.00"))
        self.assertEqual(d["caja_borrador"], "Banco")

    def test_compra_idempotente_y_auditada(self):
        self._compra()
        self.assertTrue(self._compra().json()["repetido"])
        self.negro.refresh_from_db()
        self.assertEqual(self.negro.stock_grams, Decimal("2500"))
        self.assertEqual(RegistroAgente.objects.get().operacion, "compra")

    def test_compra_invalida_no_toca_nada(self):
        r = self.c.post("/api/agentes/escritura/compras/", {"clave": "c2", "aprobo": "Lucas", "lineas": [
            {"filamento_id": self.negro.id, "cantidad": "1000"}, {"insumo_id": 9999, "cantidad": "1"}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Compra.objects.count(), 0)
        self.negro.refresh_from_db()
        self.assertEqual(self.negro.stock_grams, Decimal("500"))

    def test_gasto_queda_en_gastos_y_no_toca_stock(self):
        r = self.c.post("/api/agentes/escritura/gastos/", {
            "clave": "g1", "aprobo": "Lucas", "concepto": "Contador", "monto": "45000", "categoria": "administración",
            "medio_pago": "Transferencia"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        g = Gasto.objects.get()
        self.assertEqual((g.concepto, g.monto, g.categoria.nombre), ("Contador", Decimal("45000.00"), "Administración"))
        self.assertEqual(r.json()["caja_borrador"], "Banco")
        self.negro.refresh_from_db()
        self.assertEqual(self.negro.stock_grams, Decimal("500"))

    def test_gasto_categoria_inexistente_devuelve_las_que_hay(self):
        r = self.c.post("/api/agentes/escritura/gastos/", {"clave": "g2", "aprobo": "Lucas", "concepto": "X",
                                                           "monto": "10", "categoria": "Viajes"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Administración", r.json()["categorias"])

    def test_sin_token_no_entra(self):
        self.assertEqual(APIClient().post("/api/agentes/escritura/compras/", {}).status_code, 403)
        self.assertEqual(APIClient().post("/api/agentes/escritura/gastos/", {}).status_code, 403)
