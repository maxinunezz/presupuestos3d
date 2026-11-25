"""Escritura de Roque: token propio, idempotencia, solo lo suyo, aprobar descuenta y cancelar devuelve exacto."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inventory.models import Filament
from production.models import Maquina, ProductionJob

from .models import Presupuesto, RegistroAgente
from .tests import add_pieza, make_producto

LECTURA, ESCRITURA = "token-lectura", "token-escritura"


@override_settings(AGENTES_API_TOKEN=LECTURA, AGENTES_ESCRITURA_TOKEN=ESCRITURA)
class EscrituraAgentesTests(TestCase):
    def setUp(self):
        self.c = APIClient()
        self.c.credentials(HTTP_X_AGENTES_ESCRITURA_TOKEN=ESCRITURA)
        Maquina.objects.create(name="Bambu X1", supports_multicolor=True)
        self.rojo = Filament.objects.create(brand="Grilon", material_type="PLA", color="Rojo",
                                            cost_per_kg=Decimal("2000"), stock_grams=Decimal("2000"))
        self.estrella = make_producto(name="Cortante estrella", sale_price=Decimal("1450"))
        add_pieza(self.estrella, self.rojo, Decimal("64"), print_hours=Decimal("0.5"))
        self.sin_diseno = make_producto(name="Vaso", sale_price=Decimal("3000"), diseno_listo=False)
        add_pieza(self.sin_diseno, self.rojo, Decimal("100"), name="Vaso")
        self.estrella.verificar_costeo("Maxi")
        self.sin_diseno.verificar_costeo("Maxi")

    def _crear(self, clave="venta-1-crear", producto=None, cantidad=10):
        return self.c.post("/api/agentes/escritura/presupuestos/", {
            "clave": clave, "aprobo": "Lucas", "cliente": "Panadería Sol", "vendedor": "Miguel",
            "items": [{"producto_id": (producto or self.estrella).id, "cantidad": cantidad, "precio": "1500"}]}, format="json")

    def test_tokens_separados_y_solo_post(self):
        self.assertEqual(APIClient().post("/api/agentes/escritura/presupuestos/", {}).status_code, 403)
        lectura = APIClient()
        lectura.credentials(HTTP_X_AGENTES_TOKEN=LECTURA, HTTP_X_AGENTES_ESCRITURA_TOKEN=LECTURA)
        self.assertEqual(lectura.post("/api/agentes/escritura/presupuestos/", {}).status_code, 403)
        self.assertEqual(self.c.get("/api/agentes/escritura/presupuestos/").status_code, 403)
        escritura_lee = APIClient()
        escritura_lee.credentials(HTTP_X_AGENTES_TOKEN=ESCRITURA)
        self.assertEqual(escritura_lee.get("/api/agentes/stock/").status_code, 403)
        with override_settings(AGENTES_ESCRITURA_TOKEN=""):
            self.assertEqual(self._crear().status_code, 403)

    def test_crear_borrador_no_toca_stock_y_es_idempotente(self):
        r = self._crear()
        self.assertEqual(r.status_code, 201, r.content)
        p = Presupuesto.objects.get(pk=r.json()["id"])
        self.assertEqual((p.status, p.vendedor, p.items.get().unit_price), (Presupuesto.Status.DRAFT, "Miguel", Decimal("1500")))
        self.assertEqual(Filament.objects.get(pk=self.rojo.pk).stock_grams, Decimal("2000"))
        self.assertEqual(ProductionJob.objects.count(), 0)
        r2 = self._crear()
        self.assertTrue(r2.json()["repetido"])
        self.assertEqual(Presupuesto.objects.count(), 1)
        self.assertEqual(RegistroAgente.objects.get().aprobo, "Lucas")

    def test_aprobar_y_cancelar_devuelve_el_stock_exacto(self):
        pid = self._crear().json()["id"]
        r = self.c.post(f"/api/agentes/escritura/presupuestos/{pid}/aprobar/", {"clave": "a1", "aprobo": "Lucas"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "APPROVED")
        self.assertEqual(r.json()["trabajos"], 1)
        self.assertEqual(Filament.objects.get(pk=self.rojo.pk).stock_grams, Decimal("1360"))  # 2000 − 10 × 64
        self.assertTrue(self.c.post(f"/api/agentes/escritura/presupuestos/{pid}/aprobar/", {"clave": "a1", "aprobo": "Lucas"},
                                    format="json").json()["repetido"])
        r = self.c.post(f"/api/agentes/escritura/presupuestos/{pid}/cancelar/", {"clave": "c1", "aprobo": "Lucas"}, format="json")
        self.assertEqual(r.json()["status"], "CANCELLED")
        self.assertEqual(Filament.objects.get(pk=self.rojo.pk).stock_grams, Decimal("2000"))
        self.assertFalse(ProductionJob.objects.exclude(status=ProductionJob.Status.CANCELLED).exists())
        self.assertEqual(self.c.post(f"/api/agentes/escritura/presupuestos/{pid}/cancelar/", {"clave": "c2", "aprobo": "Lucas"},
                                     format="json").status_code, 409)

    def test_sin_diseno_no_aprueba(self):
        pid = self._crear(producto=self.sin_diseno).json()["id"]
        r = self.c.post(f"/api/agentes/escritura/presupuestos/{pid}/aprobar/", {"clave": "a2", "aprobo": "Lucas"}, format="json")
        self.assertEqual((r.status_code, r.json()["motivo"]), (409, "DisenoNoListoError"))
        self.assertEqual(Filament.objects.get(pk=self.rojo.pk).stock_grams, Decimal("2000"))

    def test_presupuestos_cargados_a_mano_tambien(self):
        from .models import PresupuestoItem

        manual = Presupuesto.objects.create(client_name="Manual")
        PresupuestoItem.objects.create(presupuesto=manual, producto=self.estrella, quantity=2)
        r = self.c.post(f"/api/agentes/escritura/presupuestos/{manual.id}/aprobar/", {"clave": "m-a", "aprobo": "Lucas"}, format="json")
        self.assertEqual((r.status_code, r.json()["cargado_por_roque"]), (200, False))
        self.assertEqual(RegistroAgente.objects.get(clave="m-a").presupuesto_id, manual.id)
        r = self.c.post(f"/api/agentes/escritura/presupuestos/{manual.id}/completar/", {"clave": "m-c", "aprobo": "Lucas"}, format="json")
        self.assertEqual(r.status_code, 409)  # tiene 1 trabajo de impresión abierto
        self.assertIn("sin terminar", r.json()["error"])
        ProductionJob.objects.filter(presupuesto=manual).update(status=ProductionJob.Status.DONE)
        r = self.c.post(f"/api/agentes/escritura/presupuestos/{manual.id}/completar/", {"clave": "m-c2", "aprobo": "Lucas"}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "COMPLETED"))
        self.assertEqual(self.c.post("/api/agentes/escritura/presupuestos/99999/aprobar/", {"clave": "x9", "aprobo": "L"},
                                     format="json").status_code, 404)

    def test_detalle_de_presupuesto_con_token_de_lectura(self):
        lectura = APIClient()
        lectura.credentials(HTTP_X_AGENTES_TOKEN=LECTURA)
        pid = self._crear().json()["id"]
        r = lectura.get(f"/api/agentes/presupuestos/{pid}/").json()
        self.assertEqual((r["cliente"], r["items"][0]["cantidad"], r["status"]), ("Panadería Sol", 10, "DRAFT"))

    def test_faltan_datos(self):
        self.assertEqual(self.c.post("/api/agentes/escritura/presupuestos/", {"aprobo": "Lucas"}, format="json").status_code, 400)
        self.assertEqual(self.c.post("/api/agentes/escritura/presupuestos/", {"clave": "x", "aprobo": "Lucas", "cliente": "A",
                                     "items": [{"producto_id": 99999, "cantidad": 1}]}, format="json").status_code, 400)
