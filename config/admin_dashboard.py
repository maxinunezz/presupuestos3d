"""
Reordena y reagrupa el índice del admin (la barra lateral y el "Inicio").

Por defecto Django ordena las apps y los modelos alfabéticamente, sin
relación con cómo se usa el negocio en el día a día. Acá se reemplaza ese
orden por uno pensado para el flujo real de trabajo:

- Primero "Métricas": los paneles de solo lectura que hoy viven repartidos
  en cada app (Panel de métricas, Panel de ventas, Panel de costos, Panel de
  gastos) — lo primero que se mira al entrar. Los tres primeros son el mismo
  motor de KPIs (budgets.metrics), separado en varias páginas (ventas/cobros;
  costos de producción; inventario/resultado) para no amontonar todo en un
  único dashboard larguísimo. "Tablero de producción" (antes acá) pasó a
  encabezar la sección "Producción", justo arriba de "Cola de producción":
  es donde se opera el día a día de las máquinas, no un panel de KPIs.
- Después las apps de negocio (Producción, Ventas y presupuestos, Inventario
  y compras, Gastos), y dentro de cada una: primero lo operativo (lo que se
  toca seguido), al final los catálogos/configuración (lo que se carga una
  vez y no se vuelve a tocar). Dentro de "Ventas y presupuestos", "Ventas"
  (el panel, antes "Panel de ventas") queda justo arriba de "Presupuestos",
  ya que es la vista de solo lectura de esos mismos pedidos ya aprobados.
  Dentro de "Inventario y compras", "Totales de inventario" (antes en
  "Métricas") pasó a encabezar la sección, seguido de "Movimientos de
  stock" (el historial completo) y recién después lo operativo (Compras,
  Filamentos, Agregados y sus categorías), dejando "Ajustes manuales de
  stock" al final por ser lo que menos se toca del día a día.
- Al final "Configuración" (usuarios, grupos, tokens de API): administración
  del sistema, no del negocio.

Se implementa parchando `AdminSite.get_app_list` (en vez de declarar un
`AdminSite` propio) para no tener que tocar ningún `admin.site.register(...)`
existente en el resto del proyecto. `_SECTIONS` es la única fuente de verdad
del orden: cualquier modelo que se registre a futuro y no se agregue ahí
sigue apareciendo igual, agrupado por su app real al final de la lista (ver
`leftovers` abajo), para que nunca quede "perdido" del índice del admin.
"""
from django.contrib.admin.sites import AdminSite
from django.utils.translation import gettext_lazy as _

# Cada sección: (slug para CSS/anclas, título, [(app_label, nombre de la
# clase del modelo), ...]). El orden de las listas define el orden en
# pantalla, tanto de las secciones como de los modelos dentro de cada una.
_SECTIONS = [
    (
        "metricas",
        _("Métricas"),
        [
            ("budgets", "Metricas"),
            ("budgets", "MetricasVentas"),
            ("budgets", "PanelCostos"),
            ("gastos", "PanelGastos"),
            ("budgets", "Socio"),
        ],
    ),
    (
        "produccion",
        _("Producción"),
        [
            ("production", "Tablero"),
            ("production", "ColaProduccion"),
            ("production", "ProductionJob"),
            ("production", "Maquina"),
        ],
    ),
    (
        "ventas_presupuestos",
        _("Ventas y presupuestos"),
        [
            ("budgets", "PanelVentas"),
            ("budgets", "Presupuesto"),
            ("budgets", "Producto"),
            ("budgets", "StockProductos"),
            ("budgets", "StockPiezas"),
        ],
    ),
    (
        "inventario_compras",
        _("Inventario y compras"),
        [
            ("inventory", "StockTotals"),
            ("inventory", "StockMovement"),
            ("inventory", "Compra"),
            ("inventory", "Filament"),
            ("inventory", "Aggregate"),
            ("inventory", "AggregateCategory"),
            ("inventory", "AjusteStock"),
        ],
    ),
    (
        "gastos",
        _("Gastos"),
        [
            ("gastos", "Gasto"),
            ("gastos", "CategoriaGasto"),
            ("gastos", "TopeGasto"),
            ("gastos", "AreaResponsable"),
            ("gastos", "MedioPagoGasto"),
        ],
    ),
    (
        "configuracion",
        _("Configuración"),
        [
            ("auth", "Group"),
            ("auth", "User"),
            ("authtoken", "TokenProxy"),
        ],
    ),
]


def _get_app_list(self, request, app_label=None):
    app_dict = self._build_app_dict(request, app_label)

    by_key = {}
    for app in app_dict.values():
        for model in app["models"]:
            by_key[(app["app_label"], model["object_name"])] = model

    used_keys = set()
    app_list = []
    for slug, title, keys in _SECTIONS:
        models = [by_key[key] for key in keys if key in by_key]
        if models:
            used_keys.update(key for key in keys if key in by_key)
            app_list.append(
                {
                    "name": title,
                    "app_label": slug,
                    # "#" no puede matchear ningún request.path real, así
                    # que esta sección "sintética" (junta modelos de varias
                    # apps distintas) nunca queda marcada como current-app
                    # por error; ver admin/app_list.html de Django.
                    "app_url": "#",
                    "has_module_perms": True,
                    "models": models,
                }
            )

    leftovers = {}
    for app in app_dict.values():
        remaining = [
            model
            for model in app["models"]
            if (app["app_label"], model["object_name"]) not in used_keys
        ]
        if remaining:
            leftover_app = dict(app)
            leftover_app["models"] = remaining
            leftovers[app["app_label"]] = leftover_app
    app_list.extend(sorted(leftovers.values(), key=lambda app: app["name"]))

    return app_list


def patch_admin_site():
    AdminSite.get_app_list = _get_app_list
