"""Borrar presupuestos: una venta aprobada/cobrada no se puede borrar (se cancela); lo que no dejó huella, sí.

Borrar no devuelve el material ni anula el cobro (quedan huérfanos en stock y caja): por eso se bloquea (27/11/2026)."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Presupuesto, PresupuestoItem
from caja.models import CuentaCaja, MovimientoCaja
from caja.tests import add_pieza, make_producto
from inventory.models import Filament


class BorrarPresupuestosTests(TestCase):
    def setUp(self):
        self.fil = Filament.objects.create(brand="M", material_type="PLA", color="C", cost_per_kg=Decimal("10000"),
                                           stock_grams=Decimal("1000"))
        CuentaCaja.objects.create(nombre="Efectivo", medio_pago_default="EFECTIVO", saldo_inicial=0,
                                  fecha_saldo_inicial=timezone.localdate() - timedelta(days=1))
        self.admin = get_user_model().objects.create_superuser("jefe", "j@x.com", "x")
        self.client.force_login(self.admin)

    def _presupuesto(self, aprobar=True, cobrar=False):
        p = make_producto(sale_price=Decimal("1000"))
        add_pieza(p, self.fil, Decimal("100"), print_hours=Decimal("1"))
        pres = Presupuesto.objects.create(client_name="Cliente", medio_pago="EFECTIVO")
        PresupuestoItem.objects.create(presupuesto=pres, producto=p, quantity=2)
        if aprobar:
            pres.approve()
        if cobrar:
            pres.estado_venta = "PAGADO"; pres.save()
            MovimientoCaja.objects.filter(presupuesto=pres).update(estado="CONFIRMADO")
        return pres

    def _borrar(self, pres):
        url = reverse("admin:budgets_presupuesto_delete", args=[pres.pk])
        return self.client.post(url, {"post": "yes"}, follow=True)

    def test_aprobado_no_se_puede_borrar_y_no_hay_boton(self):
        pres = self._presupuesto()
        self.assertTrue(pres.motivo_no_borrar())
        r = self.client.get(reverse("admin:budgets_presupuesto_change", args=[pres.pk]))
        self.assertNotContains(r, reverse("admin:budgets_presupuesto_delete", args=[pres.pk]))
        self._borrar(pres)
        self.assertTrue(Presupuesto.objects.filter(pk=pres.pk).exists())

    def test_cancelado_sin_cobro_se_borra_y_el_material_ya_volvio(self):
        pres = self._presupuesto()
        pres.cancel()
        pres.refresh_from_db(); self.fil.refresh_from_db()
        self.assertEqual(self.fil.stock_grams, Decimal("1000"))
        self.assertEqual(pres.motivo_no_borrar(), "")
        self._borrar(pres)
        self.assertFalse(Presupuesto.objects.filter(pk=pres.pk).exists())

    def test_cancelado_con_cobro_confirmado_queda_como_registro(self):
        pres = self._presupuesto(cobrar=True)
        pres.cancel()
        pres.refresh_from_db()
        self.assertIn("cobro", pres.motivo_no_borrar())
        self._borrar(pres)
        self.assertTrue(Presupuesto.objects.filter(pk=pres.pk).exists())

    def test_borrador_se_borra_con_sus_borradores_de_caja(self):
        pres = self._presupuesto(aprobar=False)
        pres.estado_venta = "PAGADO"; pres.save()   # deja un borrador de cobro sin confirmar
        self.assertTrue(MovimientoCaja.objects.filter(presupuesto=pres, estado="BORRADOR").exists())
        self._borrar(pres)
        self.assertFalse(Presupuesto.objects.filter(pk=pres.pk).exists())
        self.assertFalse(MovimientoCaja.objects.filter(estado="BORRADOR").exists())   # no queda huérfano

    def test_borrado_masivo(self):
        aprobada, borrador = self._presupuesto(), self._presupuesto(aprobar=False)
        url = reverse("admin:budgets_presupuesto_changelist")
        # con una aprobada en la selección no se borra nada (Django muestra cuál lo impide)
        self.client.post(url, {"action": "delete_selected", "_selected_action": [aprobada.pk, borrador.pk], "post": "yes"}, follow=True)
        self.assertEqual(Presupuesto.objects.filter(pk__in=[aprobada.pk, borrador.pk]).count(), 2)
        # solo borradores: se borran
        self.client.post(url, {"action": "delete_selected", "_selected_action": [borrador.pk], "post": "yes"}, follow=True)
        self.assertFalse(Presupuesto.objects.filter(pk=borrador.pk).exists())
        self.assertTrue(Presupuesto.objects.filter(pk=aprobada.pk).exists())
