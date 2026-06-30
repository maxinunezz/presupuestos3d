from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Presupuesto, PresupuestoItem, Producto

from .metrics import build_gastos_metrics
from .models import Gasto, TopeGasto


def gasto(cat, monto, dia, mes=6, anio=2026, recurrente=False, periodicidad=None):
    return Gasto.objects.create(
        categoria=cat,
        concepto=f"{cat}-{monto}",
        monto=Decimal(str(monto)),
        fecha=date(anio, mes, dia),
        es_recurrente=recurrente,
        periodicidad=periodicidad or Gasto.Periodicidad.UNICA,
    )


class GastoModelTests(TestCase):
    def test_monthly_equivalent_mensual(self):
        g = gasto(
            Gasto.Categoria.SUBSCRIPTION, 1000, 5,
            recurrente=True, periodicidad=Gasto.Periodicidad.MENSUAL,
        )
        self.assertEqual(g.monthly_equivalent, Decimal("1000.00"))

    def test_monthly_equivalent_anual_se_divide(self):
        g = gasto(
            Gasto.Categoria.IT, 1200, 5,
            recurrente=True, periodicidad=Gasto.Periodicidad.ANUAL,
        )
        self.assertEqual(g.monthly_equivalent, Decimal("100.00"))

    def test_monthly_equivalent_no_recurrente_es_cero(self):
        g = gasto(Gasto.Categoria.ADMIN, 500, 5)
        self.assertEqual(g.monthly_equivalent, Decimal("0"))


class GastosMetricsTests(TestCase):
    def setUp(self):
        gasto(Gasto.Categoria.ADMIN, 1000, 3)
        gasto(Gasto.Categoria.COMMERCIAL, 500, 10)
        gasto(
            Gasto.Categoria.SUBSCRIPTION, 300, 15,
            recurrente=True, periodicidad=Gasto.Periodicidad.MENSUAL,
        )
        # Mes anterior (mayo).
        gasto(Gasto.Categoria.ADMIN, 800, 5, mes=5)

    def test_total_y_n_gastos_del_mes(self):
        m = build_gastos_metrics(2026, 6)
        self.assertEqual(m["total_gastos"], Decimal("1800"))
        self.assertEqual(m["n_gastos"], 3)

    def test_desglose_por_categoria(self):
        m = build_gastos_metrics(2026, 6)
        admin = next(c for c in m["categorias"] if c["value"] == "ADMIN")
        self.assertEqual(admin["total"], Decimal("1000"))

    def test_run_rate_mensual_de_recurrentes(self):
        m = build_gastos_metrics(2026, 6)
        self.assertEqual(m["run_rate_mensual"], Decimal("300.00"))
        self.assertEqual(m["run_rate_anual"], Decimal("3600.00"))

    def test_comparativo_mes_anterior(self):
        m = build_gastos_metrics(2026, 6)
        self.assertEqual(m["prev_total"], Decimal("800"))
        # (1800 - 800) / 800 * 100 = 125%
        self.assertEqual(round(m["variacion_pct"], 1), Decimal("125.0"))

    def test_vista_anual_suma_todos_los_meses(self):
        m = build_gastos_metrics(2026, None)
        # 1800 (junio) + 800 (mayo) = 2600
        self.assertEqual(m["total_gastos"], Decimal("2600"))
        self.assertEqual(m["acumulado_anual"], Decimal("2600"))

    def test_run_rate_anual_usa_meses_transcurridos(self):
        # Año en curso (2026): una suscripción mensual cargada como un gasto por
        # mes (ene–jun). En vista anual el compromiso mensual debe dar ~el monto,
        # no monto×meses/12 (que subestimaría).
        Gasto.objects.all().delete()
        for mes in range(1, 7):  # ene..jun (hoy es jun 2026)
            gasto(
                Gasto.Categoria.SUBSCRIPTION, 1000, 5, mes=mes,
                recurrente=True, periodicidad=Gasto.Periodicidad.MENSUAL,
            )
        m = build_gastos_metrics(2026, None)
        self.assertEqual(m["run_rate_mensual"], Decimal("1000.00"))

    def test_ventas_excluye_para_stock_y_cancelados(self):
        prod = Producto.objects.create(name="P")
        # Pedido de cliente válido.
        ok = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(
            presupuesto=ok, producto=prod, quantity=1, unit_price=Decimal("5000")
        )
        ok.status = Presupuesto.Status.APPROVED
        ok.approved_at = timezone.make_aware(timezone.datetime(2026, 6, 12, 10, 0))
        ok.save()
        # Pedido para_stock (no es venta real) y uno cancelado: no deben sumar.
        for para_stock, status in ((True, Presupuesto.Status.APPROVED), (False, Presupuesto.Status.CANCELLED)):
            p = Presupuesto.objects.create(client_name="X", para_stock=para_stock)
            PresupuestoItem.objects.create(
                presupuesto=p, producto=prod, quantity=1, unit_price=Decimal("9999")
            )
            p.status = status
            p.approved_at = timezone.make_aware(timezone.datetime(2026, 6, 13, 10, 0))
            p.save()
        m = build_gastos_metrics(2026, 6)
        self.assertEqual(m["ventas"], Decimal("5000"))

    def test_resultado_operativo_vs_ventas(self):
        prod = Producto.objects.create(name="P")
        pres = Presupuesto.objects.create(client_name="Cliente")
        PresupuestoItem.objects.create(
            presupuesto=pres, producto=prod, quantity=1, unit_price=Decimal("5000")
        )
        pres.status = Presupuesto.Status.APPROVED
        pres.approved_at = timezone.make_aware(
            timezone.datetime(2026, 6, 12, 10, 0)
        )
        pres.save()
        m = build_gastos_metrics(2026, 6)
        self.assertEqual(m["ventas"], Decimal("5000"))
        self.assertEqual(m["resultado"], Decimal("3200"))  # 5000 - 1800

    def test_topes_detecta_exceso(self):
        TopeGasto.objects.create(
            categoria=Gasto.Categoria.ADMIN, monto_mensual=Decimal("700")
        )
        m = build_gastos_metrics(2026, 6)
        admin_tope = next(t for t in m["topes_rows"] if t["label"] == "Administración")
        self.assertTrue(admin_tope["excedido"])  # gasto 1000 > tope 700


class PanelGastosViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        gasto(Gasto.Categoria.ADMIN, 1000, 3)

    def test_panel_carga(self):
        url = reverse("admin:gastos_panelgastos_changelist")
        resp = self.client.get(url, {"year": 2026, "month": 6})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Panel de gastos")

    def test_export_xlsx(self):
        url = reverse("admin:gastos_panelgastos_changelist")
        resp = self.client.get(url, {"year": 2026, "month": 6, "export": "xlsx"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("spreadsheetml", resp["Content-Type"])
