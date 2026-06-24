from rest_framework import viewsets
from rest_framework.filters import SearchFilter

from .models import Aggregate, Filament, StockMovement
from .serializers import AggregateSerializer, FilamentSerializer, StockMovementSerializer


class FilamentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Filament.objects.all()
    serializer_class = FilamentSerializer
    filter_backends = [SearchFilter]
    search_fields = ["brand", "color", "material_type"]

    def get_queryset(self):
        qs = super().get_queryset()
        active_only = self.request.query_params.get("active_only")
        if active_only == "true":
            qs = qs.filter(is_active=True)
        return qs


class AggregateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Aggregate.objects.all()
    serializer_class = AggregateSerializer
    filter_backends = [SearchFilter]
    search_fields = ["name", "category"]

    def get_queryset(self):
        qs = super().get_queryset()
        active_only = self.request.query_params.get("active_only")
        if active_only == "true":
            qs = qs.filter(is_active=True)
        return qs


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Solo lectura: los movimientos de stock se crean automáticamente
    (al aprobar un presupuesto) o vía el admin, no directamente por API.
    """

    queryset = StockMovement.objects.select_related(
        "filament", "aggregate", "related_presupuesto"
    ).all()
    serializer_class = StockMovementSerializer
