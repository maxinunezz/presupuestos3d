from django.contrib import admin

from .models import Aggregate, Filament, StockMovement


@admin.register(Filament)
class FilamentAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "material_type",
        "color",
        "cost_per_kg",
        "cost_per_gram",
        "stock_grams",
        "is_active",
    )
    list_filter = ("material_type", "brand", "is_active")
    search_fields = ("brand", "color")
    list_editable = ("cost_per_kg", "stock_grams", "is_active")

    @admin.display(description="Costo/g")
    def cost_per_gram(self, obj):
        return f"${obj.cost_per_gram}"


@admin.register(Aggregate)
class AggregateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "unit",
        "cost_per_unit",
        "stock_quantity",
        "is_active",
    )
    list_filter = ("category", "is_active")
    search_fields = ("name",)
    list_editable = ("cost_per_unit", "stock_quantity", "is_active")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "item",
        "quantity",
        "reason",
        "related_budget",
        "note",
    )
    list_filter = ("reason",)
    readonly_fields = ("created_at",)

    @admin.display(description="Ítem")
    def item(self, obj):
        return obj.filament or obj.aggregate
