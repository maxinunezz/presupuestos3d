# Generado a mano: segundo paso de "Pieza como catálogo reutilizable entre
# Productos". Los datos ya se copiaron a ProductoPieza en la migración
# anterior; acá se quitan los campos viejos de Pieza (producto, units_needed,
# order) y se actualiza el ordering, que ahora es solo por nombre (Pieza deja
# de depender de un producto puntual para ordenarse).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0045_productopieza"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="pieza",
            name="producto",
        ),
        migrations.RemoveField(
            model_name="pieza",
            name="units_needed",
        ),
        migrations.RemoveField(
            model_name="pieza",
            name="order",
        ),
        migrations.AlterModelOptions(
            name="pieza",
            options={
                "ordering": ["name"],
                "verbose_name": "Pieza",
                "verbose_name_plural": "Piezas",
            },
        ),
    ]
