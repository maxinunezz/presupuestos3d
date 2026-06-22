from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Budget, BudgetNotApprovableError
from .pdf import budget_pdf_filename, render_budget_pdf
from .serializers import BudgetSerializer, StockShortageSerializer


class BudgetViewSet(viewsets.ModelViewSet):
    queryset = Budget.objects.prefetch_related(
        "filament_lines__filament", "aggregate_lines__aggregate"
    ).all()
    serializer_class = BudgetSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @action(detail=True, methods=["get"], url_path="check-stock")
    def check_stock(self, request, pk=None):
        """
        Devuelve los faltantes de stock sin modificar nada. Pensado para
        mostrar un warning en el front antes de que el usuario confirme
        la aprobación.
        """
        budget = self.get_object()
        shortages = budget.check_stock_availability()
        serializer = StockShortageSerializer(shortages, many=True)
        return Response({"has_shortage": len(shortages) > 0, "shortages": serializer.data})

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        Aprueba el presupuesto: descuenta stock (cappeado en 0) y registra
        los movimientos. No bloquea si falta stock, pero devuelve el detalle
        de los faltantes para que el front lo muestre como warning.
        """
        budget = self.get_object()

        try:
            shortages = budget.approve()
        except BudgetNotApprovableError as exc:
            return Response({"detail": str(exc)}, status=400)

        serializer = StockShortageSerializer(shortages, many=True)
        budget_data = BudgetSerializer(budget).data
        return Response(
            {
                "budget": budget_data,
                "has_shortage": len(shortages) > 0,
                "shortages": serializer.data,
            }
        )

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        """
        Crea una copia del presupuesto (con sus líneas), en estado Borrador y
        re-costeada a los precios actuales. Devuelve el presupuesto nuevo.
        """
        budget = self.get_object()
        copy = budget.duplicate()
        return Response(BudgetSerializer(copy).data, status=201)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """Descarga el presupuesto como PDF listo para enviar al cliente."""
        budget = self.get_object()
        pdf_bytes = render_budget_pdf(budget)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{budget_pdf_filename(budget)}"'
        )
        return response
