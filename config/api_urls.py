from rest_framework.routers import DefaultRouter

from inventory.views import AggregateViewSet, FilamentViewSet, StockMovementViewSet

# Nota: el flujo de Costeo de productos / Presupuestos (Producto, Presupuesto)
# se opera desde el admin, no por API. Acá solo se exponen los recursos de
# inventario que el front pueda necesitar.
router = DefaultRouter()
router.register("filaments", FilamentViewSet, basename="filament")
router.register("aggregates", AggregateViewSet, basename="aggregate")
router.register("stock-movements", StockMovementViewSet, basename="stockmovement")

urlpatterns = router.urls
