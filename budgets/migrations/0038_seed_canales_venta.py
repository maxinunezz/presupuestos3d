from django.db import migrations


# Los dos canales en los que se vende hoy. El dueño carga después los
# porcentajes/costos reales de cada uno desde el admin (acá arrancan en 0
# para no inventar un número de comisión/envío que no sea el real).
CANALES_INICIALES = ["Mercado Libre", "Página Web"]


def seed_canales(apps, schema_editor):
    CanalVenta = apps.get_model("budgets", "CanalVenta")
    Producto = apps.get_model("budgets", "Producto")
    ProductoCanalPrecio = apps.get_model("budgets", "ProductoCanalPrecio")

    canales = []
    for order, nombre in enumerate(CANALES_INICIALES):
        canal, _ = CanalVenta.objects.get_or_create(nombre=nombre, defaults={"order": order})
        canales.append(canal)

    # Backfill: los productos que ya existían antes de este canal también
    # tienen que mostrar su info de gastos de venta por canal, no solo los
    # que se carguen de acá en adelante (ver `Producto.save()` en models.py,
    # que cubre el alta de productos nuevos).
    productos_activos = Producto.objects.filter(is_active=True)
    for canal in canales:
        for producto in productos_activos:
            ProductoCanalPrecio.objects.get_or_create(producto=producto, canal=canal)


def eliminar_canales(apps, schema_editor):
    CanalVenta = apps.get_model("budgets", "CanalVenta")
    CanalVenta.objects.filter(nombre__in=CANALES_INICIALES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0037_canalventa_productocanalprecio"),
    ]

    operations = [
        migrations.RunPython(seed_canales, eliminar_canales),
    ]
