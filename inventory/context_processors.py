"""
Context processor para la campanita de alertas de bajo stock del admin.

Expone, en todas las páginas del admin, la lista de filamentos y agregados
que están por debajo de su stock mínimo, para mostrar el aviso (la campanita)
en la barra superior.
"""

from django.db.models import F
from django.urls import reverse
from django.utils.translation import gettext


def low_stock_alerts(request):
    # Solo calculamos para usuarios del staff dentro del admin: en el front o
    # para anónimos no hace falta (y evitamos consultas innecesarias).
    if not request.path.startswith("/admin/"):
        return {}
    user = getattr(request, "user", None)
    if user is None or not user.is_active or not user.is_staff:
        return {}

    # Import diferido para no romper si el módulo se importa antes de las apps.
    from .models import Aggregate, Filament

    items = []

    low_filaments = Filament.objects.filter(
        min_stock__gt=0, stock_grams__lt=F("min_stock")
    )
    for f in low_filaments:
        items.append(
            {
                "label": str(f),
                "current": f"{f.stock_grams:.0f} g",
                "minimum": f"{f.min_stock:.0f} g",
                "url": reverse("admin:inventory_filament_change", args=[f.pk]),
                "kind": gettext("Filamento"),
            }
        )

    low_aggregates = Aggregate.objects.filter(
        min_stock__gt=0, stock_quantity__lt=F("min_stock")
    )
    for a in low_aggregates:
        unit = a.get_unit_display()
        items.append(
            {
                "label": a.name,
                "current": f"{a.stock_quantity:.0f} {unit}",
                "minimum": f"{a.min_stock:.0f} {unit}",
                "url": reverse("admin:inventory_aggregate_change", args=[a.pk]),
                "kind": gettext("Agregado"),
            }
        )

    return {
        "low_stock_items": items,
        "low_stock_count": len(items),
    }
