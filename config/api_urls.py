from rest_framework.routers import DefaultRouter

from budgets.views import BudgetViewSet
from inventory.views import AggregateViewSet, FilamentViewSet, StockMovementViewSet

router = DefaultRouter()
router.register("filaments", FilamentViewSet, basename="filament")
router.register("aggregates", AggregateViewSet, basename="aggregate")
router.register("stock-movements", StockMovementViewSet, basename="stockmovement")
router.register("budgets", BudgetViewSet, basename="budget")

urlpatterns = router.urls
