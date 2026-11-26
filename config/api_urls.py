from django.urls import path
from rest_framework.routers import DefaultRouter

from budgets.api_views import (
    PedidoOnlineCreateView,
    ProductoCostoPorSkuView,
    ReporteEjecutivoMensualView,
)
from budgets.api_agentes import (
    CajaAgentes,
    ComprasAgentes,
    GastosAgentes,
    PresupuestosAgentes,
    ProduccionAgentes,
    ReporteMensualAgentes,
    StockAgentes,
)
from budgets.api_agentes_ventas import (
    CosteosPendientesAgentes,
    TrabajosImpresosAgentes,
    PrecioRapidoAgentes,
    PresupuestoDetalleAgentes,
    ProductoAgentes,
    ProductosAgentes,
    SimularPedidoAgentes,
)
from budgets.api_agentes_escritura import (
    CrearProductoEscritura,
    CorregirCosteoEscritura,
    VerificarCosteoEscritura,
    CrearCompraEscritura,
    CrearGastoEscritura,
    AprobarPresupuestoEscritura,
    CancelarPresupuestoEscritura,
    CompletarPresupuestoEscritura,
    CrearPresupuestoEscritura,
)
from inventory.views import AggregateViewSet, FilamentViewSet, StockMovementViewSet

# Nota: el flujo de Costeo de productos / Presupuestos (Producto, Presupuesto)
# se opera desde el admin, no por API. Acá solo se exponen los recursos de
# inventario que el front pueda necesitar, más el endpoint del reporte
# ejecutivo mensual (para Zapier, ver budgets/api_views.py), el de alta de
# avisos de venta online y el de costo por SKU (ambos para 3darg-backend, ver
# mismo archivo).
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
    path(
        "pedidos-online/",
        PedidoOnlineCreateView.as_view(),
        name="pedido-online-create",
    ),
    # Solo lectura para los agentes de IA (token propio, ver budgets/api_agentes.py)
    path("agentes/stock/", StockAgentes.as_view(), name="agentes-stock"),
    path("agentes/produccion/", ProduccionAgentes.as_view(), name="agentes-produccion"),
    path("agentes/presupuestos/", PresupuestosAgentes.as_view(), name="agentes-presupuestos"),
    path("agentes/caja/", CajaAgentes.as_view(), name="agentes-caja"),
    path("agentes/gastos/", GastosAgentes.as_view(), name="agentes-gastos"),
    path("agentes/compras/", ComprasAgentes.as_view(), name="agentes-compras"),
    path("agentes/reporte-mensual/", ReporteMensualAgentes.as_view(), name="agentes-reporte-mensual"),
    path("agentes/productos/", ProductosAgentes.as_view(), name="agentes-productos"),
    path("agentes/productos/<int:pk>/", ProductoAgentes.as_view(), name="agentes-producto"),
    path("agentes/simular-pedido/", SimularPedidoAgentes.as_view(), name="agentes-simular-pedido"),
    path("agentes/precio-rapido/", PrecioRapidoAgentes.as_view(), name="agentes-precio-rapido"),
    path("agentes/presupuestos/<int:pk>/", PresupuestoDetalleAgentes.as_view(), name="agentes-presupuesto"),
    path("agentes/costeos-pendientes/", CosteosPendientesAgentes.as_view(), name="agentes-costeos-pendientes"),
    path("agentes/trabajos-impresos/", TrabajosImpresosAgentes.as_view(), name="agentes-trabajos-impresos"),
    # Escritura de Roque (token aparte, solo POST, auditada): ver budgets/api_agentes_escritura.py
    path("agentes/escritura/presupuestos/", CrearPresupuestoEscritura.as_view(), name="agentes-escritura-crear"),
    path("agentes/escritura/presupuestos/<int:pk>/aprobar/", AprobarPresupuestoEscritura.as_view(),
         name="agentes-escritura-aprobar"),
    path("agentes/escritura/presupuestos/<int:pk>/cancelar/", CancelarPresupuestoEscritura.as_view(),
         name="agentes-escritura-cancelar"),
    path("agentes/escritura/presupuestos/<int:pk>/completar/", CompletarPresupuestoEscritura.as_view(),
         name="agentes-escritura-completar"),
    path("agentes/escritura/compras/", CrearCompraEscritura.as_view(), name="agentes-escritura-compra"),
    path("agentes/escritura/gastos/", CrearGastoEscritura.as_view(), name="agentes-escritura-gasto"),
    path("agentes/escritura/productos/", CrearProductoEscritura.as_view(), name="agentes-escritura-producto"),
    path("agentes/escritura/productos/<int:pk>/verificar-costeo/", VerificarCosteoEscritura.as_view(),
         name="agentes-escritura-verificar-costeo"),
    path("agentes/escritura/piezas/<int:pk>/corregir-costeo/", CorregirCosteoEscritura.as_view(),
         name="agentes-escritura-corregir-costeo"),
    path(
        "productos/costo/",
        ProductoCostoPorSkuView.as_view(),
        name="producto-costo-por-sku",
    ),
]
