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

    def test_compra_con_insumo_nuevo_lo_da_de_alta_con_fecha_y_caja(self):
        """Caso real 27/11: «Pegamento Unipox 100 ml x2 a 16872, Mercado Libre, pagué con Mercado Pago el 17/10»."""
        CuentaCaja.objects.update_or_create(nombre="Mercado Pago", defaults={"medio_pago_default": "MERCADOPAGO",
                                                                            "fecha_saldo_inicial": date(2026, 1, 1)})
        r = self.c.post("/api/agentes/escritura/compras/", {
            "clave": "unipox", "aprobo": "Lucas", "proveedor": "Mercado Libre", "medio_pago": "Mercado Pago",
            "fecha": "2026-10-17", "lineas": [{"nuevo_insumo": {"nombre": "Pegamento Unipox 100 ml", "unidad": "UNIT"},
                                              "cantidad": "2", "precio_total": "16872"}]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        unipox = Aggregate.objects.get(name="Pegamento Unipox 100 ml")
        self.assertEqual((unipox.stock_quantity, unipox.cost_per_unit), (Decimal("2"), Decimal("8436")))
        self.assertEqual(r.json()["altas"][0]["id"], unipox.id)
        compra = Compra.objects.get()
        self.assertEqual(compra.confirmed_at.date().isoformat(), "2026-10-17")
        mov = MovimientoCaja.objects.get()
        self.assertEqual((mov.cuenta.nombre, mov.monto, mov.fecha.isoformat()), ("Mercado Pago", Decimal("16872"), "2026-10-17"))

    def test_insumo_nuevo_repetido_o_unidad_invalida_no_crea_nada(self):
        r = self.c.post("/api/agentes/escritura/compras/", {"clave": "x", "aprobo": "Lucas", "lineas": [
            {"nuevo_insumo": {"nombre": "bolsa", "unidad": "UNIT"}, "cantidad": "1", "precio_total": "10"}]}, format="json")
        self.assertEqual(r.status_code, 409)
        r = self.c.post("/api/agentes/escritura/compras/", {"clave": "y", "aprobo": "Lucas", "lineas": [
            {"nuevo_insumo": {"nombre": "Unipox", "unidad": "MM"}, "cantidad": "1", "precio_total": "10"}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Aggregate.objects.count(), 1)

    def test_insumo_nuevo_en_mililitros(self):
        """2 pomos de 100 ml a $16.872 → 200 ml en stock a $84,36 el ml (para saber cuánto queda)."""
        r = self.c.post("/api/agentes/escritura/compras/", {"clave": "ml", "aprobo": "Maxi", "lineas": [
            {"nuevo_insumo": {"nombre": "Pegamento Unipox", "unidad": "ML", "notas": "Pomo de 100 ml"},
             "cantidad": "200", "precio_total": "16872"}]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        a = Aggregate.objects.get(name="Pegamento Unipox")
        self.assertEqual((a.unit, a.stock_quantity, a.cost_per_unit), ("ML", Decimal("200"), Decimal("84.36")))

    def test_compra_filamento_nuevo(self):
        r = self.c.post("/api/agentes/escritura/compras/", {"clave": "f", "aprobo": "Maxi", "lineas": [
            {"nuevo_filamento": {"marca": "Tp3d", "material": "PETG", "color": "Rojo"}, "cantidad": "1000",
             "precio_total": "20000"}]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        f = Filament.objects.get(brand="Tp3d")
        self.assertEqual((f.stock_grams, f.cost_per_kg, f.material_type), (Decimal("1000"), Decimal("20000"), "PETG"))

    def test_gasto_recurrente_en_cuotas_con_area(self):
        from gastos.models import AreaResponsable

        AreaResponsable.objects.get_or_create(nombre="IT")
        r = self.c.post("/api/agentes/escritura/gastos/", {"clave": "g10", "aprobo": "Maxi", "concepto": "Claude",
                        "monto": "20", "categoria": "Administración", "area": "it", "periodicidad": "MENSUAL"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        g = Gasto.objects.get(concepto="Claude")
        self.assertEqual((g.area.nombre, g.es_recurrente, g.periodicidad), ("IT", True, "MENSUAL"))
        r = self.c.post("/api/agentes/escritura/gastos/", {"clave": "g11", "aprobo": "Maxi", "concepto": "Impresora",
                        "monto": "100000", "categoria": "Administración", "cuota_actual": 1, "cuotas_totales": 6}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Gasto.objects.get(concepto="Impresora").cuotas_totales, 6)
        r = self.c.post("/api/agentes/escritura/gastos/", {"clave": "g12", "aprobo": "Maxi", "concepto": "X",
                        "monto": "1", "categoria": "Administración", "area": "Marte"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_gasto_con_mercado_pago_crea_el_medio(self):
        r = self.c.post("/api/agentes/escritura/gastos/", {"clave": "g9", "aprobo": "Lucas", "concepto": "Envío",
                        "monto": "3000", "categoria": "Administración", "medio_pago": "Mercado Pago"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["medio_pago"], "Mercado Pago")

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
