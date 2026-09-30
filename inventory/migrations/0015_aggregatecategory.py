# Convierte Aggregate.category de un choice fijo en código (CharField con 4
# opciones hardcodeadas) a una FK a un modelo real (AggregateCategory),
# editable desde el admin: así se puede agregar una categoría nueva (ej.
# "Repuestos") sin programar ni migrar nada.
#
# Se hace en un solo archivo con varios pasos, todos dentro de la misma
# transacción de migración, para no dejar la base en un estado intermedio:
#   1) Crea el modelo AggregateCategory.
#   2) Siembra las 4 categorías que ya existían como choices (mismo texto).
#   3) Agrega un campo FK temporal (category_fk) a Aggregate.
#   4) Copia cada Aggregate existente al FK, según su código viejo.
#   5) Borra el CharField viejo y renombra category_fk -> category.
#   6) Deja el campo final (obligatorio, con default), y actualiza el
#      ordering del modelo para usar el nombre de la categoría.

from django.db import migrations, models
import django.db.models.deletion

import inventory.models

# Mapea el código viejo (el que tenía cada choice) al nombre visible, que es
# lo único que persiste ahora (ya no hay código interno).
CODIGO_A_NOMBRE = {
    "HARDWARE": "Herraje (argollas, llaveros, etc.)",
    "PACKAGING": "Packaging",
    "DECORATION": "Decoración (pegatinas, etc.)",
    "OTHER": "Otro",
}


def sembrar_categorias(apps, schema_editor):
    AggregateCategory = apps.get_model("inventory", "AggregateCategory")
    for nombre in CODIGO_A_NOMBRE.values():
        AggregateCategory.objects.get_or_create(nombre=nombre)


def copiar_categoria_a_fk(apps, schema_editor):
    AggregateCategory = apps.get_model("inventory", "AggregateCategory")
    Aggregate = apps.get_model("inventory", "Aggregate")

    categorias = {c.nombre: c for c in AggregateCategory.objects.all()}

    for agg in Aggregate.objects.all():
        nombre = CODIGO_A_NOMBRE.get(agg.category, "Otro")
        agg.category_fk = categorias[nombre]
        agg.save(update_fields=["category_fk"])


def copiar_fk_a_categoria(apps, schema_editor):
    """Reversa: vuelve a dejar el código viejo a partir del nombre de la FK."""
    NOMBRE_A_CODIGO = {v: k for k, v in CODIGO_A_NOMBRE.items()}
    Aggregate = apps.get_model("inventory", "Aggregate")

    for agg in Aggregate.objects.select_related("category_fk").all():
        agg.category = NOMBRE_A_CODIGO.get(agg.category_fk.nombre, "OTHER")
        agg.save(update_fields=["category"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0014_aggregate_notes"),
    ]

    operations = [
        migrations.CreateModel(
            name="AggregateCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=50, unique=True, verbose_name="Nombre")),
            ],
            options={
                "verbose_name": "Categoría de agregado",
                "verbose_name_plural": "Categorías de agregado",
                "ordering": ["nombre"],
            },
        ),
        migrations.RunPython(sembrar_categorias, migrations.RunPython.noop),
        migrations.AddField(
            model_name="aggregate",
            name="category_fk",
            field=models.ForeignKey(
                to="inventory.aggregatecategory",
                on_delete=django.db.models.deletion.PROTECT,
                null=True,
                related_name="aggregates",
                verbose_name="Categoría",
            ),
        ),
        migrations.RunPython(copiar_categoria_a_fk, copiar_fk_a_categoria),
        migrations.RemoveField(model_name="aggregate", name="category"),
        migrations.RenameField(model_name="aggregate", old_name="category_fk", new_name="category"),
        migrations.AlterField(
            model_name="aggregate",
            name="category",
            field=models.ForeignKey(
                to="inventory.aggregatecategory",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="aggregates",
                verbose_name="Categoría",
                default=inventory.models._default_aggregate_category,
            ),
        ),
        migrations.AlterModelOptions(
            name="aggregate",
            options={
                "verbose_name": "Agregado",
                "verbose_name_plural": "Agregados",
                "ordering": ["category__nombre", "name"],
            },
        ),
    ]
