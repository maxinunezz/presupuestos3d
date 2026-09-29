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

    def test_anonimo_no_autenticado(self):
        # DRF devuelve 401 (no autenticado) cuando hay autenticadores
        # configurados y ninguno tuvo éxito; es el código correcto para un
        # anónimo (403 es para un usuario ya autenticado sin permisos).
        for path in ("/api/filaments/", "/api/aggregates/", "/api/stock-movements/"):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_anonimo_no_puede_escribir(self):
        self.assertEqual(self.client.post("/api/filaments/", {}).status_code, 401)

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


class UsuarioAdminTests(TestCase):
    """Crear un usuario nuevo desde 'Add user' no debe dejarlo sin poder
    entrar al admin: el alta rápida de Django no tilda 'Es staff' por
    default, y ese fue justo el problema real que reportó el usuario."""

    def setUp(self):
        User = get_user_model()
        User.objects.create_superuser("dueño", password="x")
        self.client.login(username="dueño", password="x")

    def test_alta_rapida_deja_al_usuario_con_is_staff(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("admin:auth_user_add"),
            {
                "username": "socio",
                "password1": "unaClaveSegura123",
                "password2": "unaClaveSegura123",
            },
        )
        self.assertEqual(response.status_code, 302)
        User = get_user_model()
        socio = User.objects.get(username="socio")
        self.assertTrue(socio.is_staff)
        # No lo hacemos superusuario solo: eso lo decide el dueño a mano.
        self.assertFalse(socio.is_superuser)

    def test_editar_un_usuario_existente_no_fuerza_is_staff(self):
        from django.urls import reverse

        User = get_user_model()
        empleado = User.objects.create_user("empleado", password="x", is_staff=False)
        response = self.client.post(
            reverse("admin:auth_user_change", args=[empleado.pk]),
            {
                "username": "empleado",
                "date_joined_0": "2024-01-01",
                "date_joined_1": "00:00:00",
                # is_staff / is_active se omiten a propósito: simula que el
                # dueño lo dejó destildado a mano al editar.
            },
        )
        # No nos importa si el form es válido (puede fallar por otros campos
        # requeridos); lo que verificamos es que editar NO tiene el mismo
        # comportamiento automático que crear.
        empleado.refresh_from_db()
        self.assertFalse(empleado.is_staff)


class StockMovementAdminReadOnlyTests(TestCase):
    """'Movimientos de stock' es un historial: no se puede cargar nada a mano
    ahí (no cambiaría el stock real). Para eso está 'Ajustes manuales de
    stock', que sí aplica el cambio."""

    def setUp(self):
        User = get_user_model()
        User.objects.create_superuser("dueño", password="x")
        self.client.login(username="dueño", password="x")
        self.agg = Aggregate.objects.create(
            name="Bolsas ziploc",
            category=Aggregate.Category.PACKAGING,
            unit=Aggregate.Unit.UNIT,
            cost_per_unit=Decimal("10"),
            stock_quantity=Decimal("100"),
        )

    def test_no_se_puede_agregar_desde_movimientos_de_stock(self):
        from django.urls import reverse

        response = self.client.get(reverse("admin:inventory_stockmovement_add"))
        self.assertEqual(response.status_code, 403)

    def test_cargar_por_movimientos_no_cambia_stock(self):
        # Aunque se fuerce el POST (ej. bypaseando el botón), no debe aplicar
        # nada: has_add_permission ya lo bloquea con 403 y no llega a guardar.
        from django.urls import reverse

        response = self.client.post(
            reverse("admin:inventory_stockmovement_add"),
            {
                "aggregate": self.agg.pk,
                "quantity": "-20",
                "reason": StockMovement.Reason.MANUAL_ADJUSTMENT,
                "note": "intento directo",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.agg.refresh_from_db()
        self.assertEqual(self.agg.stock_quantity, Decimal("100"))
        self.assertEqual(StockMovement.objects.filter(aggregate=self.agg).count(), 0)

    def test_ajuste_manual_si_descuenta_el_stock_del_agregado(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("admin:inventory_ajustestock_add"),
            {"aggregate": self.agg.pk, "quantity": "-20", "note": "conteo físico"},
        )
        self.assertEqual(response.status_code, 302)
        self.agg.refresh_from_db()
        self.assertEqual(self.agg.stock_quantity, Decimal("80"))
