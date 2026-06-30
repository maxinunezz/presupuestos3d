from django.db import migrations


def crear_piezas_desde_productos(apps, schema_editor):
    """
    Para cada Producto existente crea una 'Pieza principal' que hereda sus horas
    de máquina y mueve sus líneas de filamento (ProductoFilamentLine) a la nueva
    pieza (PiezaFilamentLine). Así no se pierde el costeo ya cargado.
    """
    Producto = apps.get_model("budgets", "Producto")
    Pieza = apps.get_model("budgets", "Pieza")
    PiezaFilamentLine = apps.get_model("budgets", "PiezaFilamentLine")

    for producto in Producto.objects.all():
        if producto.piezas.exists():
            continue  # re-ejecución: no duplicar

        filament_lines = list(producto.filament_lines.all())
        pieza = Pieza.objects.create(
            producto=producto,
            name="Pieza principal",
            units_needed=1,
            pieces_per_gcode=1,
            print_time_hours=producto.print_time_hours,
            # Multicolor heredado: si el producto era multicolor o tenía más de
            # una línea de filamento, la pieza necesita AMS.
            requires_ams=bool(producto.is_multicolor) or len(filament_lines) > 1,
            stock_quantity=0,
            order=0,
        )
        for line in filament_lines:
            PiezaFilamentLine.objects.create(
                pieza=pieza,
                filament=line.filament,
                grams_used=line.grams_used,
                unit_cost=line.unit_cost,
            )


def borrar_piezas(apps, schema_editor):
    """Reversa: borra todas las piezas (vuelve al estado anterior por producto)."""
    Pieza = apps.get_model("budgets", "Pieza")
    Pieza.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0013_pieza_piezafilamentline"),
    ]

    operations = [
        migrations.RunPython(crear_piezas_desde_productos, borrar_piezas),
    ]
