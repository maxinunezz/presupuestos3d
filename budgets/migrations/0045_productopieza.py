# Generado a mano: primer paso de "Pieza como catálogo reutilizable entre
# Productos". Crea la tabla intermedia ProductoPieza y migra a ella los datos
# que hoy viven directo en Pieza (producto/units_needed/order). Los campos
# viejos de Pieza TODAVÍA existen en este paso — se eliminan en la migración
# siguiente, una vez que los datos ya están copiados.

import django.db.models.deletion
from django.db import migrations, models


def migrar_piezas_a_productopieza(apps, schema_editor):
    Pieza = apps.get_model("budgets", "Pieza")
    ProductoPieza = apps.get_model("budgets", "ProductoPieza")
    for pieza in Pieza.objects.all():
        ProductoPieza.objects.create(
            producto_id=pieza.producto_id,
            pieza_id=pieza.pk,
            units_needed=pieza.units_needed,
            order=pieza.order,
        )


def noop(apps, schema_editor):
    # No es reversible de forma general: si una Pieza ya se compartió entre
    # varios Productos (varias filas de ProductoPieza para la misma Pieza),
    # no hay una única fila de Pieza.producto/units_needed/order a la que
    # volver. Revertir esta migración borra ProductoPieza sin intentar
    # reconstruir los campos viejos de Pieza.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0044_renombrar_ecommerce_cutter_size_a_template_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductoPieza",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "units_needed",
                    models.PositiveIntegerField(
                        default=1,
                        help_text="Cuántas unidades de esta pieza lleva UN producto.",
                        verbose_name="Unidades necesarias por producto",
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0, verbose_name="Orden")),
                (
                    "pieza",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="producto_piezas",
                        to="budgets.pieza",
                    ),
                ),
                (
                    "producto",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="producto_piezas",
                        to="budgets.producto",
                    ),
                ),
            ],
            options={
                "verbose_name": "Pieza del producto",
                "verbose_name_plural": "Piezas del producto",
                "ordering": ["order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="productopieza",
            constraint=models.UniqueConstraint(
                fields=("producto", "pieza"), name="unique_producto_pieza"
            ),
        ),
        migrations.RunPython(migrar_piezas_a_productopieza, noop),
    ]
