# Convierte Gasto.categoria / TopeGasto.categoria de un choice fijo en código
# (CharField con 5 opciones hardcodeadas) a una FK a un modelo real
# (CategoriaGasto), editable desde el admin: así se puede agregar una
# categoría nueva (ej. "Herramientas") sin programar ni migrar.
#
# Se hace en un solo archivo con varios pasos, todos dentro de la misma
# transacción de migración, para no dejar la base en un estado intermedio:
#   1) Crea el modelo CategoriaGasto.
#   2) Siembra las 5 categorías que ya existían como choices (mismo texto).
#   3) Agrega un campo FK temporal (categoria_fk) a Gasto y TopeGasto.
#   4) Copia cada Gasto/TopeGasto existente al FK, según su código viejo.
#   5) Borra el CharField viejo y renombra categoria_fk -> categoria.
#   6) Deja el campo final (obligatorio, con default en Gasto y unique en
#      TopeGasto, igual que estaba antes).

from django.db import migrations, models
import django.db.models.deletion

import gastos.models

# Mapea el código viejo (el que tenía cada choice) al nombre visible, que es
# lo único que persiste ahora (ya no hay código interno).
CODIGO_A_NOMBRE = {
    "ADMIN": "Administración",
    "COMMERCIAL": "Comercialización",
    "SUBSCRIPTION": "Suscripciones",
    "IT": "IT",
    "OTHER": "Otro",
}


def sembrar_categorias(apps, schema_editor):
    CategoriaGasto = apps.get_model("gastos", "CategoriaGasto")
    for nombre in CODIGO_A_NOMBRE.values():
        CategoriaGasto.objects.get_or_create(nombre=nombre)


def copiar_categoria_a_fk(apps, schema_editor):
    CategoriaGasto = apps.get_model("gastos", "CategoriaGasto")
    Gasto = apps.get_model("gastos", "Gasto")
    TopeGasto = apps.get_model("gastos", "TopeGasto")

    categorias = {c.nombre: c for c in CategoriaGasto.objects.all()}

    for gasto in Gasto.objects.all():
        nombre = CODIGO_A_NOMBRE.get(gasto.categoria, "Otro")
        gasto.categoria_fk = categorias[nombre]
        gasto.save(update_fields=["categoria_fk"])

    for tope in TopeGasto.objects.all():
        nombre = CODIGO_A_NOMBRE.get(tope.categoria, "Otro")
        tope.categoria_fk = categorias[nombre]
        tope.save(update_fields=["categoria_fk"])


def copiar_fk_a_categoria(apps, schema_editor):
    """Reversa: vuelve a dejar el código viejo a partir del nombre de la FK."""
    NOMBRE_A_CODIGO = {v: k for k, v in CODIGO_A_NOMBRE.items()}
    Gasto = apps.get_model("gastos", "Gasto")
    TopeGasto = apps.get_model("gastos", "TopeGasto")

    for gasto in Gasto.objects.select_related("categoria_fk").all():
        gasto.categoria = NOMBRE_A_CODIGO.get(gasto.categoria_fk.nombre, "OTHER")
        gasto.save(update_fields=["categoria"])

    for tope in TopeGasto.objects.select_related("categoria_fk").all():
        tope.categoria = NOMBRE_A_CODIGO.get(tope.categoria_fk.nombre, "OTHER")
        tope.save(update_fields=["categoria"])


class Migration(migrations.Migration):

    dependencies = [
        ("gastos", "0002_gasto_tipo"),
    ]

    operations = [
        migrations.CreateModel(
            name="CategoriaGasto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=50, unique=True, verbose_name="Nombre")),
            ],
            options={
                "verbose_name": "Categoría de gasto",
                "verbose_name_plural": "Categorías de gasto",
                "ordering": ["nombre"],
            },
        ),
        migrations.RunPython(sembrar_categorias, migrations.RunPython.noop),
        migrations.AddField(
            model_name="gasto",
            name="categoria_fk",
            field=models.ForeignKey(
                to="gastos.categoriagasto",
                on_delete=django.db.models.deletion.PROTECT,
                null=True,
                related_name="gastos",
                verbose_name="Categoría",
            ),
        ),
        migrations.AddField(
            model_name="topegasto",
            name="categoria_fk",
            field=models.ForeignKey(
                to="gastos.categoriagasto",
                on_delete=django.db.models.deletion.PROTECT,
                null=True,
                related_name="topes",
                verbose_name="Categoría",
            ),
        ),
        migrations.RunPython(copiar_categoria_a_fk, copiar_fk_a_categoria),
        migrations.RemoveField(model_name="gasto", name="categoria"),
        migrations.RemoveField(model_name="topegasto", name="categoria"),
        migrations.RenameField(model_name="gasto", old_name="categoria_fk", new_name="categoria"),
        migrations.RenameField(model_name="topegasto", old_name="categoria_fk", new_name="categoria"),
        migrations.AlterField(
            model_name="gasto",
            name="categoria",
            field=models.ForeignKey(
                to="gastos.categoriagasto",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="gastos",
                verbose_name="Categoría",
                default=gastos.models._default_categoria_gasto,
            ),
        ),
        migrations.AlterField(
            model_name="topegasto",
            name="categoria",
            field=models.ForeignKey(
                to="gastos.categoriagasto",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="topes",
                verbose_name="Categoría",
                unique=True,
            ),
        ),
        migrations.AlterModelOptions(
            name="topegasto",
            options={
                "ordering": ["categoria__nombre"],
                "verbose_name": "Tope de gasto (presupuesto)",
                "verbose_name_plural": "Topes de gasto (presupuestos)",
            },
        ),
    ]
