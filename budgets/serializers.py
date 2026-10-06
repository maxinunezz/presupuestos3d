from rest_framework import serializers

from .models import PedidoOnline


class PedidoOnlineSerializer(serializers.ModelSerializer):
    """
    Serializer de solo alta: lo usa `PedidoOnlineCreateView`, invocada por
    3darg-backend cuando una Order pasa a PAID. Nada de lo que manda
    3darg-backend se recalcula acá — es un snapshot tal cual llega.
    """

    class Meta:
        model = PedidoOnline
        fields = [
            "id",
            "external_reference",
            "brand_slug",
            "brand_name",
            "customer_email",
            "items",
            "total_amount",
            "currency",
            "order_created_at",
            "raw_payload",
        ]
        read_only_fields = ["id"]
