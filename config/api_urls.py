from django.urls import path
from rest_framework.routers import DefaultRouter

from budgets.api_views import ReporteEjecutivoMensualView
from inventory.views import AggregateViewSet, FilamentViewSet, StockMovementViewSet

# Nota: el flujo de Costeo de productos / Presupuestos (Producto, Presupuesto)
# se opera desde el admin, no por API. Acá solo se exponen los recursos de
# inventario que el front pueda necesitar, más el endpoint del reporte
# ejecutivo mensual (para Zapier, ver budgets/api_views.py).
router = DefaultRouter()
router.register("filaments", FilamentViewSet, basename="filament")
router.register("aggregates", AggregateViewSet, basename="aggregate")
router.register("stock-movements", StockMovementViewSet, basename="stockmovement")

urlpatterns = router.urls + [
    path(
        "reportes/ejecutivo-mensual/",
        ReporteEjecutivoMensualView.as_view(),
        name="reporte-ejecutivo-mensual",
    ),
]
