# Generated manually: backfill de fecha_cobro para pedidos ya Pagados.

from django.db import migrations


def backfill_fecha_cobro(apps, schema_editor):
    """
    Para los Presupuesto ya marcados como PAGADO que todavía no tienen
    fecha_cobro, la completamos con la fecha de aprobación (approved_at) como
    mejor aproximación disponible. Si no tienen approved_at, los dejamos sin
    fecha_cobro (no hay de dónde sacarla).
    """
    Presupuesto = apps.get_model("budgets", "Presupuesto")
    qs = Presupuesto.objects.filter(
        estado_venta="PAGADO", fecha_cobro__isnull=True
    ).only("id", "approved_at", "fecha_cobro", "estado_venta")

    to_update = []
    for presupuesto in qs:
        if presupuesto.approved_at is not None:
            presupuesto.fecha_cobro = presupuesto.approved_at.date()
            to_update.append(presupuesto)

    if to_update:
        Presupuesto.objects.bulk_update(to_update, ["fecha_cobro"])


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0032_panelcaja_presupuesto_fecha_cobro"),
    ]

    operations = [
        migrations.RunPython(backfill_fecha_cobro, migrations.RunPython.noop),
    ]
