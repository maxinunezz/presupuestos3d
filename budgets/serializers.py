from decimal import Decimal

from rest_framework import serializers

from inventory.models import Aggregate, Filament
from inventory.serializers import AggregateSerializer, FilamentSerializer

from .models import Budget, BudgetAggregateLine, BudgetFilamentLine


class BudgetFilamentLineSerializer(serializers.ModelSerializer):
    filament_detail = FilamentSerializer(source="filament", read_only=True)
    line_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = BudgetFilamentLine
        fields = [
            "id",
            "filament",
            "filament_detail",
            "grams_used",
            "unit_cost",
            "line_cost",
        ]
        # unit_cost es opcional: si no se manda, el modelo lo congela solo con el
        # precio vigente del filamento al guardar la línea.
        extra_kwargs = {"unit_cost": {"required": False}}


class BudgetAggregateLineSerializer(serializers.ModelSerializer):
    aggregate_detail = AggregateSerializer(source="aggregate", read_only=True)
    line_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = BudgetAggregateLine
        fields = [
            "id",
            "aggregate",
            "aggregate_detail",
            "quantity",
            "unit_cost",
            "line_cost",
        ]
        extra_kwargs = {"unit_cost": {"required": False}}


class BudgetSerializer(serializers.ModelSerializer):
    """
    Serializer principal de presupuesto. Permite crear/actualizar el
    presupuesto junto con todas sus líneas de filamento y agregados en
    un solo request (lo que necesita el front para el formulario de carga).
    """

    filament_lines = BudgetFilamentLineSerializer(many=True, required=False)
    aggregate_lines = BudgetAggregateLineSerializer(many=True, required=False)

    # Costos por UNA pieza
    material_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    material_waste_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    aggregate_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    machine_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    labor_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    unit_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    # Totales del PEDIDO
    production_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Budget
        fields = [
            "id",
            "client_name",
            "name",
            "description",
            "quantity",
            "print_time_hours",
            "machine_cost_per_hour",
            "waste_percent",
            "post_processing_hours",
            "labor_cost_per_hour",
            "fixed_cost",
            "margin_percent",
            "round_to",
            "status",
            "approved_at",
            "filament_lines",
            "aggregate_lines",
            "material_cost",
            "material_waste_cost",
            "aggregate_cost",
            "machine_cost",
            "labor_cost",
            "unit_cost",
            "production_cost",
            "subtotal",
            "total",
            "unit_price",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "approved_at", "created_at", "updated_at"]

    def create(self, validated_data):
        filament_lines_data = validated_data.pop("filament_lines", [])
        aggregate_lines_data = validated_data.pop("aggregate_lines", [])

        budget = Budget.objects.create(**validated_data)
        self._sync_lines(budget, filament_lines_data, aggregate_lines_data)
        return budget

    def update(self, instance, validated_data):
        filament_lines_data = validated_data.pop("filament_lines", None)
        aggregate_lines_data = validated_data.pop("aggregate_lines", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Si no se mandaron líneas en el PATCH/PUT, no las tocamos.
        if filament_lines_data is not None or aggregate_lines_data is not None:
            self._sync_lines(
                instance,
                filament_lines_data if filament_lines_data is not None else None,
                aggregate_lines_data if aggregate_lines_data is not None else None,
            )
        return instance

    @staticmethod
    def _sync_lines(budget, filament_lines_data, aggregate_lines_data):
        """
        Reemplaza por completo las líneas existentes con las nuevas.
        Simplifica mucho el front: siempre se manda el set completo de
        líneas en cada guardado, en vez de manejar altas/bajas parciales.
        """
        if filament_lines_data is not None:
            budget.filament_lines.all().delete()
            for line_data in filament_lines_data:
                BudgetFilamentLine.objects.create(budget=budget, **line_data)

        if aggregate_lines_data is not None:
            budget.aggregate_lines.all().delete()
            for line_data in aggregate_lines_data:
                BudgetAggregateLine.objects.create(budget=budget, **line_data)


class StockShortageSerializer(serializers.Serializer):
    type = serializers.CharField()
    item = serializers.CharField()
    needed = serializers.DecimalField(max_digits=10, decimal_places=2)
    available = serializers.DecimalField(max_digits=10, decimal_places=2)
    missing = serializers.DecimalField(max_digits=10, decimal_places=2)
