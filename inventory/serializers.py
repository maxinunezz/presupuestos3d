from rest_framework import serializers

from .models import Aggregate, Filament, StockMovement


class FilamentSerializer(serializers.ModelSerializer):
    cost_per_gram = serializers.DecimalField(
        max_digits=10, decimal_places=4, read_only=True
    )

    class Meta:
        model = Filament
        fields = [
            "id",
            "brand",
            "material_type",
            "color",
            "color_hex",
            "cost_per_kg",
            "cost_per_gram",
            "stock_grams",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AggregateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Aggregate
        fields = [
            "id",
            "name",
            "category",
            "unit",
            "cost_per_unit",
            "stock_quantity",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class StockMovementSerializer(serializers.ModelSerializer):
    item_display = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "filament",
            "aggregate",
            "item_display",
            "quantity",
            "reason",
            "related_budget",
            "note",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_item_display(self, obj):
        item = obj.filament or obj.aggregate
        return str(item) if item else None
