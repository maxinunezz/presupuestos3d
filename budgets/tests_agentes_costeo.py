"""Costeo verificado: Roque no descuenta stock con datos de producción sin revisar; verificar y corregir."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inventory.models import Filament
from production.models import Maquina

from .models import Presupuesto, RegistroAgente
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

    def test_trabajos_impresos_trae_lo_de_la_ficha(self):
        self.assertEqual(self.r.get("/api/agentes/trabajos-impresos/?desde=2026-01-01T00:00:00").json()["trabajos"], [])
