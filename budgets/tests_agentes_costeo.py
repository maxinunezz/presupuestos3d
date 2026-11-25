"""Costeo verificado: Roque no descuenta stock con datos de producción sin revisar; verificar y corregir."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inventory.models import Filament
from production.models import Maquina

from .models import Presupuesto, Producto, RegistroAgente
from .tests import add_pieza, make_producto

LECTURA, ESCRITURA = "token-lectura", "token-escritura"


@override_settings(AGENTES_API_TOKEN=LECTURA, AGENTES_ESCRITURA_TOKEN=ESCRITURA)
class CosteoVerificadoTests(TestCase):
    def setUp(self):
        self.w = APIClient()
        self.w.credentials(HTTP_X_AGENTES_ESCRITURA_TOKEN=ESCRITURA)
        self.r = APIClient()
        self.r.credentials(HTTP_X_AGENTES_TOKEN=LECTURA)
        Maquina.objects.create(name="Bambu X1", supports_multicolor=True)
        self.rojo = Filament.objects.create(brand="Grilon", material_type="PLA", color="Rojo",
                                            cost_per_kg=Decimal("2000"), stock_grams=Decimal("2000"))
        self.cortante = make_producto(name="Cortante 8cm", sale_price=Decimal("2000"))
        add_pieza(self.cortante, self.rojo, Decimal("75"), print_hours=Decimal("3.33"))
        self.pieza = self.cortante.producto_piezas.get().pieza

    def _ficha(self):
        return self.r.get(f"/api/agentes/productos/{self.cortante.id}/").json()["costeo"]

    def test_ficha_muestra_los_datos_por_impresion(self):
        f = self._ficha()
        self.assertFalse(f["verificado"])
        pz = f["piezas"][0]
        self.assertEqual(pz["filamentos"][0]["gramos_por_impresion"], "75.00")
        self.assertIn("piezas_por_impresion", pz)

    def test_sin_verificar_no_se_aprueba_por_roque(self):
        pid = self.w.post("/api/agentes/escritura/presupuestos/", {
            "clave": "c1", "aprobo": "Lucas", "cliente": "Sol",
            "items": [{"producto_id": self.cortante.id, "cantidad": 2, "precio": "2000"}]}, format="json").json()["id"]
        r = self.w.post(f"/api/agentes/escritura/presupuestos/{pid}/aprobar/", {"clave": "a1", "aprobo": "Lucas"},
                        format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["motivo"], "CosteoSinVerificar")
        self.assertEqual(Presupuesto.objects.get(pk=pid).status, Presupuesto.Status.DRAFT)
        self.assertEqual(Filament.objects.get(pk=self.rojo.pk).stock_grams, Decimal("2000"))
        self.w.post(f"/api/agentes/escritura/productos/{self.cortante.id}/verificar-costeo/",
                    {"clave": "v1", "aprobo": "Maxi"}, format="json")
        r = self.w.post(f"/api/agentes/escritura/presupuestos/{pid}/aprobar/", {"clave": "a2", "aprobo": "Lucas"},
                        format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def test_verificar_y_se_cae_si_cambia_el_costeo(self):
        r = self.w.post(f"/api/agentes/escritura/productos/{self.cortante.id}/verificar-costeo/",
                        {"clave": "v1", "aprobo": "Maxi"}, format="json")
        self.assertTrue(r.json()["verificado"])
        self.assertEqual(self._ficha()["verificado_por"], "Maxi")
        self.assertEqual(RegistroAgente.objects.get().operacion, "verificar")
        linea = self.pieza.filament_lines.get()
        r = self.w.post(f"/api/agentes/escritura/piezas/{self.pieza.id}/corregir-costeo/",
                        {"clave": "k1", "aprobo": "Lucas", "piezas_por_impresion": 6, "minutos": "190",
                         "gramos": {str(linea.id): "72"}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["despues"]["minutos"], "190.00")
        f = self._ficha()
        self.assertFalse(f["verificado"])
        self.assertTrue(f["cambio_despues"])
        pend = self.r.get("/api/agentes/costeos-pendientes/").json()
        self.assertEqual([p["nombre"] for p in pend["productos"]], ["Cortante 8cm"])

    def test_corregir_invalido_no_toca_nada(self):
        r = self.w.post(f"/api/agentes/escritura/piezas/{self.pieza.id}/corregir-costeo/",
                        {"clave": "k2", "aprobo": "Lucas", "minutos": "-3"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.pieza.refresh_from_db()
        self.assertEqual(self.pieza.print_time_minutes, Decimal("199.80"))

    def test_detalle_trae_lo_que_movio_en_el_stock(self):
        self.cortante.verificar_costeo("Maxi")
        pid = self.w.post("/api/agentes/escritura/presupuestos/", {
            "clave": "c9", "aprobo": "Lucas", "cliente": "Sol",
            "items": [{"producto_id": self.cortante.id, "cantidad": 2, "precio": "2000"}]}, format="json").json()["id"]
        self.w.post(f"/api/agentes/escritura/presupuestos/{pid}/aprobar/", {"clave": "a9", "aprobo": "Lucas"}, format="json")
        d = self.r.get(f"/api/agentes/presupuestos/{pid}/").json()
        fil = [m for m in d["movimientos"] if m["tipo"] == "filamento"]
        self.assertEqual(len(fil), 1)
        self.assertEqual(fil[0]["stock_hoy"], str(Filament.objects.get(pk=self.rojo.pk).stock_grams))
        self.assertTrue(d["trabajos"])

    def test_costo_promedio_y_ultima_venta(self):
        d = self.r.get(f"/api/agentes/productos/{self.cortante.id}/").json()
        self.assertIsNone(d["ultima_venta"])
        self.assertEqual(d["costo_unitario"], str(self.cortante.unit_cost_avg))
        for clave, precio, cliente in (("u1", "1800", "Sol"), ("u2", "2100", "Luna")):
            self.w.post("/api/agentes/escritura/presupuestos/", {
                "clave": clave, "aprobo": "Lucas", "cliente": cliente,
                "items": [{"producto_id": self.cortante.id, "cantidad": 3, "precio": precio}]}, format="json")
        u = self.r.get(f"/api/agentes/productos/{self.cortante.id}/").json()["ultima_venta"]
        self.assertEqual((u["precio"], u["cliente"], u["cantidad"]), ("2100.00", "Luna", 3))
        Presupuesto.objects.filter(client_name="Luna").update(status=Presupuesto.Status.CANCELLED)
        u = self.r.get(f"/api/agentes/productos/{self.cortante.id}/").json()["ultima_venta"]
        self.assertEqual(u["cliente"], "Sol")  # las canceladas no cuentan

    def test_trabajos_impresos_trae_lo_de_la_ficha(self):
        self.assertEqual(self.r.get("/api/agentes/trabajos-impresos/?desde=2026-01-01T00:00:00").json()["trabajos"], [])


class VerificarSinSincronizarTiendaTests(TestCase):
    """Prueba real del 26/11/2026: verificar daba 500 porque guardaba el producto y la sincronización con la tienda
    tardaba más de 10 s."""

    def test_verificar_no_llama_a_la_tienda(self):
        from unittest import mock

        fil = Filament.objects.create(brand="G", material_type="PLA", color="Rojo", cost_per_kg=Decimal("2000"),
                                      stock_grams=Decimal("100"))
        p = make_producto(name="Cortante tienda", sale_price=Decimal("2000"))
        add_pieza(p, fil, Decimal("10"))
        with mock.patch("config.api_3darg.sync_costeo_ecommerce") as sync:
            p.verificar_costeo("Maxi")
        self.assertFalse(sync.called)
        self.assertTrue(Producto.objects.get(pk=p.pk).costeo_verificado)

    def test_si_la_tienda_tarda_no_hay_error(self):
        from types import SimpleNamespace
        from unittest import mock

        from config import api_3darg

        prod = SimpleNamespace(pk=1, name="X", sku="X", ecommerce_template_name="X", sale_price=Decimal("1"),
                               weight_kg=None, length_cm=None, width_cm=None, height_cm=None)
        with override_settings(API_3DARG_URL="https://tienda", API_3DARG_TOKEN="t"), \
             mock.patch.object(api_3darg.urllib.request, "urlopen", side_effect=TimeoutError("lento")):
            self.assertIn("error", api_3darg.sync_costeo_ecommerce(prod))
