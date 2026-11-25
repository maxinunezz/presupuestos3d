"""API de ventas para Roque: solo lectura, simulación sin efectos y precio rápido."""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inventory.models import Aggregate, Filament
from production.models import Maquina, ProductionJob

from .models import Pieza, Producto, ProductoAggregateLine
from .tests import add_pieza, make_producto

TOKEN = "token-de-prueba"


@override_settings(AGENTES_API_TOKEN=TOKEN)
class VentasAgentesTests(TestCase):
    def setUp(self):
        self.c = APIClient()
        self.c.credentials(HTTP_X_AGENTES_TOKEN=TOKEN)
        Maquina.objects.create(name="Bambu X1", supports_multicolor=True)
        self.rojo = Filament.objects.create(brand="Grilon", material_type="PLA", color="Rojo",
                                            cost_per_kg=Decimal("2000"), stock_grams=Decimal("2000"))
        self.estrella = make_producto(name="Cortante estrella", sale_price=Decimal("1450"),
                                      post_processing_minutes=Decimal("6"), labor_cost_per_hour=Decimal("6000"))
        add_pieza(self.estrella, self.rojo, Decimal("64"), print_hours=Decimal("0.5"))
        self.bolsa = Aggregate.objects.create(name="Bolsa", cost_per_unit=Decimal("20"), stock_quantity=Decimal("30"))
        ProductoAggregateLine.objects.create(producto=self.estrella, aggregate=self.bolsa, quantity=Decimal("1"))
        self.sin_diseno = make_producto(name="Cortante corazón", sale_price=Decimal("1500"), diseno_listo=False)
        add_pieza(self.sin_diseno, self.rojo, Decimal("50"), name="Corazón")

    def _get(self, ruta, **params):
        return self.c.get(f"/api/agentes/{ruta}/", params)

    def test_token_y_solo_lectura(self):
        self.assertEqual(APIClient().get("/api/agentes/productos/").status_code, 403)
        self.assertEqual(self.c.post("/api/agentes/simular-pedido/", {}).status_code, 403)
        with override_settings(AGENTES_API_TOKEN=""):
            self.assertEqual(self._get("productos").status_code, 403)

    def test_buscar_marca_coincidencia_exacta_primero(self):
        r = self._get("productos", q="cortante estrella").json()
        self.assertTrue(r["productos"][0]["exacto"])
        self.assertEqual(r["productos"][0]["id"], self.estrella.id)
        r = self._get("productos", q="estrella").json()
        self.assertFalse(any(p["exacto"] for p in r["productos"]))
        self.assertEqual(r["productos"][0]["id"], self.estrella.id)

    def test_costeo(self):
        r = self._get(f"productos/{self.estrella.id}").json()
        self.assertEqual(r["precio_general"], "1450.00")
        self.assertEqual(r["filamentos"][0]["gramos"], "64.00")
        self.assertTrue(r["diseno_listo"])
        self.assertEqual(self._get("productos/99999").status_code, 404)

    def test_simular_no_guarda_nada_y_calcula_faltantes(self):
        antes = (Filament.objects.get(pk=self.rojo.pk).stock_grams, Aggregate.objects.get(pk=self.bolsa.pk).stock_quantity,
                 ProductionJob.objects.count(), list(Pieza.objects.values_list("stock_quantity", flat=True)))
        r = self._get("simular-pedido", items=f"{self.estrella.id}:50:1500").json()
        despues = (Filament.objects.get(pk=self.rojo.pk).stock_grams, Aggregate.objects.get(pk=self.bolsa.pk).stock_quantity,
                   ProductionJob.objects.count(), list(Pieza.objects.values_list("stock_quantity", flat=True)))
        self.assertEqual(antes, despues)
        f = r["filamentos"][0]
        self.assertEqual(f["necesita_g"], "3200.00")       # 50 × 64 g
        self.assertEqual(f["falta_g"], "1200.00")          # hay 2000 g
        self.assertEqual(r["insumos"][0]["falta"], "20.00")  # 50 bolsas, hay 30
        item = r["items"][0]
        self.assertEqual(item["comparacion"], "mayor")
        self.assertEqual(item["total"], "75000.00")
        self.assertEqual(r["impresion"]["horas"], "25.00")  # 50 corridas de media hora
        self.assertEqual(r["impresion"]["maquinas"], ["Bambu X1"])
        self.assertEqual(r["horas_post"], "5.00")          # 50 × 6 min

    def test_simular_precio_menor_y_diseno(self):
        r = self._get("simular-pedido", items=f"{self.estrella.id}:2:1000,{self.sin_diseno.id}:1").json()
        self.assertEqual(r["items"][0]["comparacion"], "menor")
        self.assertEqual(r["items"][1]["comparacion"], "igual")  # sin precio → usa el general
        self.assertFalse(r["items"][1]["diseno_listo"])

    def test_simular_usa_stock_de_terminados(self):
        Producto.objects.filter(pk=self.estrella.pk).update(stock_quantity=10)
        r = self._get("simular-pedido", items=f"{self.estrella.id}:10:1450").json()
        self.assertEqual(r["items"][0]["a_producir"], 0)
        self.assertEqual(r["filamentos"], [])
        self.assertEqual(r["impresion"]["trabajos"], 0)

    def test_simular_errores(self):
        self.assertEqual(self._get("simular-pedido").status_code, 400)
        self.assertEqual(self._get("simular-pedido", items="x:y").status_code, 400)
        self.assertEqual(self._get("simular-pedido", items="99999:1").status_code, 404)

    def test_precio_rapido(self):
        r = self._get("precio-rapido", material="PLA", gramos="120", horas="5", post_min="10").json()
        self.assertGreater(Decimal(r["precio"]), Decimal(r["costo"]))
        self.assertEqual(Decimal(r["precio"]) % 100, 0)    # redondeado a la centena
        self.assertEqual(self._get("precio-rapido").status_code, 400)
