from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Aggregate, Compra, CompraLine, Filament, StockMovement


class FilamentStockTests(TestCase):
    def setUp(self):
        self.fil = Filament.objects.create(
            brand="Marca",
            material_type=Filament.MaterialType.PLA,
            color="Rojo",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("500"),
        )

    def test_cost_per_gram(self):
        self.assertEqual(self.fil.cost_per_gram, Decimal("10.0000"))

    def test_deduct_stock_normal(self):
        shortage = self.fil.deduct_stock(Decimal("200"))
        self.fil.refresh_from_db()
        self.assertEqual(shortage, Decimal("0"))
        self.assertEqual(self.fil.stock_grams, Decimal("300"))

    def test_deduct_stock_no_negativo_y_devuelve_faltante(self):
        shortage = self.fil.deduct_stock(Decimal("800"))
        self.fil.refresh_from_db()
        # No queda negativo y reporta lo que faltó.
        self.assertEqual(self.fil.stock_grams, Decimal("0"))
        self.assertEqual(shortage, Decimal("300"))

    def test_is_low_stock(self):
        self.fil.min_stock = Decimal("1000")
        self.assertTrue(self.fil.is_low_stock)
        self.fil.min_stock = Decimal("0")
        self.assertFalse(self.fil.is_low_stock)


class CompraConfirmTests(TestCase):
    def setUp(self):
        self.fil = Filament.objects.create(
            brand="Marca",
            material_type=Filament.MaterialType.PLA,
            color="Azul",
            cost_per_kg=Decimal("10000"),
            stock_grams=Decimal("100"),
        )

    def test_confirm_suma_stock_actualiza_precio_y_registra_movimiento(self):
        compra = Compra.objects.create()
        CompraLine.objects.create(
            compra=compra,
            filament=self.fil,
            quantity=Decimal("1000"),
            unit_price=Decimal("12000"),
        )
        compra.confirm()
        self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("1100"))
        self.assertEqual(self.fil.cost_per_kg, Decimal("12000"))
        self.assertEqual(compra.status, Compra.Status.CONFIRMED)
        self.assertIsNotNone(compra.confirmed_at)
        mov = StockMovement.objects.get(
            filament=self.fil, reason=StockMovement.Reason.PURCHASE
        )
        self.assertEqual(mov.quantity, Decimal("1000"))

    def test_confirm_es_idempotente(self):
        compra = Compra.objects.create()
        CompraLine.objects.create(
            compra=compra, filament=self.fil, quantity=Decimal("500"), unit_price=None
        )
        compra.confirm()
        from .models import CompraNotConfirmableError

        with self.assertRaises(CompraNotConfirmableError):
            compra.confirm()
        self.fil.refresh_from_db()
        # No sumó dos veces.
        self.assertEqual(self.fil.stock_grams, Decimal("600"))


class ApiPermissionTests(TestCase):
    """C1: la API de inventario exige staff y es de solo lectura."""

    def setUp(self):
        self.client = APIClient()
        Filament.objects.create(
            brand="M",
            material_type=Filament.MaterialType.PLA,
            color="C",
            cost_per_kg=Decimal("1000"),
            stock_grams=Decimal("10"),
        )
        Aggregate.objects.create(name="Argolla", cost_per_unit=Decimal("5"))

    def test_anonimo_forbidden(self):
        for path in ("/api/filaments/", "/api/aggregates/", "/api/stock-movements/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)

    def test_anonimo_no_puede_escribir(self):
        self.assertEqual(self.client.post("/api/filaments/", {}).status_code, 403)

    def test_staff_puede_leer(self):
        User = get_user_model()
        User.objects.create_user("admin", password="x", is_staff=True)
        self.client.login(username="admin", password="x")
        self.assertEqual(self.client.get("/api/filaments/").status_code, 200)

    def test_staff_no_puede_escribir_solo_lectura(self):
        User = get_user_model()
        User.objects.create_user("admin", password="x", is_staff=True)
        self.client.login(username="admin", password="x")
        # ReadOnlyModelViewSet: POST/DELETE no permitidos (405).
        self.assertEqual(self.client.post("/api/filaments/", {}).status_code, 405)
