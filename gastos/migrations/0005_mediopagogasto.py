# Convierte Gasto.medio_pago de un choice fijo en código (CharField con 5
# opciones hardcodeadas) a una FK a un modelo real (MedioPagoGasto), editable
# desde el admin: así se puede agregar un medio de pago nuevo (ej: "Ualá")
# sin programar ni migrar. Mismo patrón que 0003_categoriagasto.py.
#
#   1) Crea el modelo MedioPagoGasto.
#   2) Siembra los 5 medios que ya existían como choices (mismo texto).
#   3) Agrega un campo FK temporal (medio_pago_fk) a Gasto.
#   4) Copia cada Gasto existente al FK, según su código viejo (vacío -> sin
#      medio de pago asignado).
#   5) Borra el CharField viejo y renombra medio_pago_fk -> medio_pago.
#   6) Deja el campo final (opcional, igual que estaba antes).

from django.db import migrations, models
import django.db.models.deletion

# Mapea el código viejo al nombre visible, que es lo único que persiste
# ahora (ya no hay código interno). "" (sin elegir) no se mapea: queda null.
CODIGO_A_NOMBRE = {
    "EFECTIVO": "Efectivo",
    "TRANSFERENCIA": "Transferencia",
    "TARJETA": "Tarjeta de crédito",
    "DEBITO": "Débito automático",
    "OTRO": "Otro",
}


def sembrar_medios_pago(apps, schema_editor):
    MedioPagoGasto = apps.get_model("gastos", "MedioPagoGasto")
    for nombre in CODIGO_A_NOMBRE.values():
        MedioPagoGasto.objects.get_or_create(nombre=nombre)


def copiar_medio_pago_a_fk(apps, schema_editor):
    MedioPagoGasto = apps.get_model("gastos", "MedioPagoGasto")
    Gasto = apps.get_model("gastos", "Gasto")

    medios = {m.nombre: m for m in MedioPagoGasto.objects.all()}

    for gasto in Gasto.objects.all():
        nombre = CODIGO_A_NOMBRE.get(gasto.medio_pago)
        gasto.medio_pago_fk = medios[nombre] if nombre else None
        gasto.save(update_fields=["medio_pago_fk"])


def copiar_fk_a_medio_pago(apps, schema_editor):
    """Reversa: vuelve a dejar el código viejo a partir del nombre de la FK."""
    NOMBRE_A_CODIGO = {v: k for k, v in CODIGO_A_NOMBRE.items()}
    Gasto = apps.get_model("gastos", "Gasto")

    for gasto in Gasto.objects.select_related("medio_pago_fk").all():
        if gasto.medio_pago_fk_id:
            gasto.medio_pago = NOMBRE_A_CODIGO.get(gasto.medio_pago_fk.nombre, "OTRO")
        else:
            gasto.medio_pago = ""
        gasto.save(update_fields=["medio_pago"])


class Migration(migrations.Migration):

    dependencies = [
        ("gastos", "0004_arearesponsable_gasto_area"),
    ]

    operations = [
        migrations.CreateModel(
            name="MedioPagoGasto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=50, unique=True, verbose_name="Nombre")),
            ],
            options={
                "verbose_name": "Medio de pago",
                "verbose_name_plural": "Medios de pago",
                "ordering": ["nombre"],
            },
        ),
        migrations.RunPython(sembrar_medios_pago, migrations.RunPython.noop),
        migrations.AddField(
            model_name="gasto",
            name="medio_pago_fk",
            field=models.ForeignKey(
                to="gastos.mediopagogasto",
                on_delete=django.db.models.deletion.PROTECT,
                null=True,
                blank=True,
                related_name="gastos",
                verbose_name="Medio de pago",
            ),
        ),
        migrations.RunPython(copiar_medio_pago_a_fk, copiar_fk_a_medio_pago),
        migrations.RemoveField(model_name="gasto", name="medio_pago"),
        migrations.RenameField(model_name="gasto", old_name="medio_pago_fk", new_name="medio_pago"),
        migrations.AlterField(
            model_name="gasto",
            name="medio_pago",
            field=models.ForeignKey(
                to="gastos.mediopagogasto",
                on_delete=django.db.models.deletion.PROTECT,
                null=True,
                blank=True,
                related_name="gastos",
                verbose_name="Medio de pago",
            ),
        ),
    ]
