from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from caja.models import CuentaCaja, MovimientoCaja
from gastos.models import CategoriaGasto, Gasto, TopeGasto
from inventory.models import Filament

from .models import Presupuesto

TOKEN = "token-de-prueba-agentes"
URLS = ["stock", "produccion", "presupuestos", "caja", "gastos", "reporte-mensual"]


@override_settings(AGENTES_API_TOKEN=TOKEN)
class ApiAgentesTests(TestCase):
    def setUp(self):
        self.c = APIClient()
        self.ok = {"HTTP_X_AGENTES_TOKEN": TOKEN}

    def test_sin_token_o_token_malo_responde_403(self):
        for u in URLS:
            self.assertEqual(self.c.get(f"/api/agentes/{u}/").status_code, 403, u)
            self.assertEqual(self.c.get(f"/api/agentes/{u}/", HTTP_X_AGENTES_TOKEN="otro").status_code, 403, u)

    @override_settings(AGENTES_API_TOKEN="")
    def test_sin_token_configurado_todo_cerrado(self):
        self.assertEqual(self.c.get("/api/agentes/stock/", HTTP_X_AGENTES_TOKEN="").status_code, 403)

    def test_no_se_puede_escribir_aunque_el_token_sea_valido(self):
        for metodo in ("post", "put", "patch", "delete"):
            r = getattr(self.c, metodo)("/api/agentes/stock/", {}, format="json", **self.ok)
            self.assertIn(r.status_code, (403, 405), metodo)

    def test_el_token_de_agentes_no_abre_la_api_vieja(self):
        self.assertIn(self.c.get("/api/filaments/", **self.ok).status_code, (401, 403))

    def test_todos_los_endpoints_responden_con_token(self):
        for u in URLS:
            self.assertEqual(self.c.get(f"/api/agentes/{u}/", **self.ok).status_code, 200, u)

    def test_stock_marca_lo_que_esta_bajo(self):
        Filament.objects.create(brand="Grilon", material_type="PLA", color="Negro", cost_per_kg=Decimal("20000"),
                                stock_grams=Decimal("200"), min_stock=Decimal("1000"))
        Filament.objects.create(brand="Grilon", material_type="PLA", color="Rojo", cost_per_kg=Decimal("20000"),
                                stock_grams=Decimal("5000"), min_stock=Decimal("1000"))
        d = self.c.get("/api/agentes/stock/", **self.ok).json()
        self.assertEqual(len(d["filamentos"]), 2)
        self.assertEqual([x["color"] for x in d["bajo_stock"]], ["Negro"])

    def test_presupuestos_con_total_y_filtro(self):
        Presupuesto.objects.create(client_name="Panadería Sol")
        d = self.c.get("/api/agentes/presupuestos/?estado=draft", **self.ok).json()
        self.assertEqual(d["presupuestos"][0]["cliente"], "Panadería Sol")
        self.assertIn("total", d["presupuestos"][0])
        self.assertEqual(self.c.get("/api/agentes/presupuestos/?estado=APPROVED", **self.ok).json()["presupuestos"], [])

    def test_caja_saldo_e_ingresos(self):
        cuenta = CuentaCaja.objects.create(nombre="MP", saldo_inicial=Decimal("1000"), fecha_saldo_inicial=date(2026, 1, 1))
        MovimientoCaja.objects.create(cuenta=cuenta, fecha=date.today(), tipo="INGRESO", monto=Decimal("500"),
                                      concepto="Seña", estado="CONFIRMADO")
        d = self.c.get("/api/agentes/caja/", **self.ok).json()
        self.assertEqual(d["cuentas"][0]["saldo"], "1500.00")
        self.assertEqual(d["ingresos_confirmados"], "500.00")

    def test_gastos_por_categoria_y_tope_superado(self):
        cat = CategoriaGasto.objects.create(nombre="Publicidad")
        Gasto.objects.create(categoria=cat, concepto="Meta Ads", monto=Decimal("30000"), fecha=date.today())
        TopeGasto.objects.create(categoria=cat, monto_mensual=Decimal("20000"))
        d = self.c.get("/api/agentes/gastos/", **self.ok).json()
        self.assertEqual(d["por_categoria"]["Publicidad"], "30000.00")
        self.assertTrue(d["topes"][0]["superado"])


class SaludTests(TestCase):
    def test_salud_responde_ok_con_el_host_del_healthcheck(self):
        r = self.client.get("/salud/", HTTP_HOST="healthcheck.railway.app")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
