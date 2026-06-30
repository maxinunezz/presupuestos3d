# CLAUDE.md

Contexto del proyecto para Claude. Leé esto antes de tocar nada.

## Qué es

Backend de gestión de **3darg**, un negocio de impresión 3D multi-marca en
Argentina. Es una app **Django** que centraliza el costeo de productos, los
presupuestos a clientes, el inventario (filamentos y agregados), las compras de
insumos, la cola de producción de las impresoras y un panel de métricas.

**Casi todo se opera desde el Django admin**, no desde una API. La API REST solo
expone recursos de inventario para un futuro front en Next.js (que corre aparte).

El dueño (usuario) escribe y prefiere las respuestas en **español (Argentina)**.
Construye la web él mismo; no es programador full-time, así que el código y los
mensajes del admin priorizan claridad.

## Stack

- **Python 3.12**, **Django 6.0.6**, **Django REST Framework 3.17**.
- **SQLite** en local (`db.sqlite3`); **Postgres/Neon** en producción vía
  `DATABASE_URL`.
- **whitenoise** sirve los estáticos (storage con manifest comprimido).
- **xhtml2pdf (pisa)** para PDFs. **openpyxl** para export a Excel.
  **Chart.js** (servido local, no CDN) para los gráficos del admin.
- Deploy en **Vercel** (serverless). `DEBUG=0` en prod; `ALLOWED_HOSTS` incluye
  `.vercel.app`. El build debe correr `collectstatic`.

## Cómo correr (IMPORTANTE)

`python` no está en el PATH global y el `activate` falla si no estás primero en
la carpeta del proyecto. Siempre:

```bash
cd /home/pmaximiliano/presupuestos3d && source venv/bin/activate
python manage.py runserver 8001     # corre en el puerto 8001
python manage.py check
python manage.py makemigrations
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py shell -c "..."     # para probar lógica
```

## Estructura

```
config/          # proyecto Django (settings, urls, api_urls, wsgi/asgi)
inventory/       # filamentos, agregados, stock, movimientos, compras
budgets/         # costeo de productos, presupuestos, PDF cliente, métricas
production/      # máquinas, trabajos de impresión, scheduler, tableros
templates/       # templates a nivel proyecto (override de admin/base_site.html)
scripts/         # generar_instructivo.py (PDF manual del admin, standalone)
staticfiles/     # salida de collectstatic (artefacto)
media/           # uploads locales (.3mf/gcode) — efímero en prod
```

## Apps y modelos

### inventory
- **Filament**: pool de filamento (marca + tipo + color). Campos: `brand`,
  `material_type` (PLA/PETG/ABS/TPU/ASA/NYLON/OTHER), `color`, `cost_per_kg`,
  `stock_grams`, `min_stock`. Props: `cost_per_gram`, `is_low_stock`.
  ⚠️ No tiene campo `name` (es brand/material_type/color).
- **Aggregate**: insumos no-filamento (herrajes, packaging, etc.). `cost_per_unit`,
  `stock_quantity`, `min_stock`, `unit` (UNIT/PAIR/METER/GRAM).
- **StockMovement**: historial de stock. `reason` (PURCHASE/BUDGET_APPROVED/
  PRODUCTION/MANUAL_ADJUSTMENT/REPRINT_FAILURE). CheckConstraint:
  exactamente uno de {filament, aggregate} no-nulo. FKs `on_delete=PROTECT`.
- **Compra** + **CompraLine**: orden de compra; al `confirm()` suma stock,
  actualiza precios y registra movimientos. Idempotente (solo desde DRAFT).
- Proxies de admin: **StockTotals** (página "Totales de inventario"),
  **AjusteStock** (ajuste manual de stock).

### budgets
- **Producto**: costeo de un producto que se compone de **Piezas**. Los gramos
  de filamento y las horas de máquina viven en cada **Pieza** (el producto suma
  las piezas para sus totales). El producto define máquina ($/h), merma, mano de
  obra (post-proceso, por producto), agregados (por producto) y margen.
  `is_multicolor` / `needs_ams` (alguna pieza con AMS) → solo máquinas con AMS.
  `priority` (Alta=1/Media=2/Baja=3/Sin prioridad=9, default Sin): ordena la cola
  de producción; prioridad más alta entra antes, "Sin prioridad" va al final. Es
  la clave primaria de orden en el scheduler, la cola y el tablero.
  Props: `total_filament_grams`, `total_machine_hours`, `material_cost`,
  `aggregate_cost`, `machine_cost`, `labor_cost`, `unit_cost`, `unit_price`,
  `aggregated_filament()`.
- **Pieza**: una pieza física del producto. `units_needed` (por producto),
  `pieces_per_gcode` (cuántas salen por corrida), `print_time_hours` (por
  corrida), `requires_ams` (auto si >1 línea de filamento), `stock_quantity`
  (unidades ya impresas en stock). `gcode_runs = ceil(units_needed/ppg)`;
  filamento y horas se multiplican por las corridas.
- **PiezaFilamentLine** (`grams_used` por corrida, `unit_cost` congelado) /
  **ProductoAggregateLine** (`quantity`, `unit_cost` congelado).
  `stock_quantity` / `min_stock`: **stock de productos terminados** (armados,
  listos para entregar sin imprimir). Props `is_low_stock` / `stock_to_make`.
- Proxy de admin: **StockPiezas** (página "Stock de piezas") y **StockProductos**
  (página "Stock de productos terminados": stock de armados vs. mínimo).
- **Presupuesto**: cotización a un cliente. `status` (DRAFT/SENT/APPROVED/
  IN_PRODUCTION/COMPLETED/CANCELLED), `fixed_cost`, fechas por estado
  (`sent_at`, `approved_at`, `production_started_at`, `production_finished_at`,
  `completed_at`), `due_date`. Props: `items_total`, `subtotal`, `total`,
  `total_pieces`, `total_print_hours`, `estimated_delivery`.
  - `approve()`: pasa a APPROVED, setea `approved_at`, descuenta inventario y
    genera la cola. **Descuenta stock AL APROBAR** (ver "Piezas y stock" abajo).
    Devuelve `{"from_stock": [...], "shortages": [...]}`. Idempotente vía
    `stock_provisioned`.
- **PresupuestoItem**: línea de presupuesto. `quantity`, `unit_price` (congelado
  al guardar). Props: `effective_unit_price`, `line_total`.
- Proxy de admin: **Metricas** (panel de KPIs, ver abajo).

### production
- **Maquina**: una impresora. `is_active`, `supports_multicolor` (la Bambu Lab
  con AMS sí, la Ender no). `cost_per_hour` (costo horario manual, se carga en
  alta/edición) y `total_hours_printed` (horas impresas acumuladas, de solo
  lectura). Prop `accumulated_depreciation` = horas impresas × costo por hora.
  `recalc_printed_hours()` recalcula desde cero sumando las horas de los
  trabajos DONE de la máquina; se dispara al marcar un trabajo como Impreso.
- **ProductionJob**: una **pieza** (campo `pieza`; `producto` queda de contexto)
  de un presupuesto en la cola de una máquina. `quantity` = unidades a imprimir.
  `status` (PENDING/PRINTING/DONE/CANCELLED), `order`, `machine`,
  `estimated_start/estimated_print_end` (snapshot del scheduler), `started_at`,
  `finished_at`, `stock_consumed`, `surplus_added`. Props: `gcode_runs`,
  `units_printed`, `surplus_units`, `print_hours`, `is_open`,
  `requires_multicolor`. `consume_stock()` descuenta el filamento (al aprobar);
  `register_surplus()` suma la sobrante al stock de la pieza (al imprimir).
  Trabajos viejos sin `pieza` siguen el modo anterior (a nivel producto).
- **HistorialImpresion**: registro histórico (snapshot) de un trabajo de una
  máquina. Lo crea automáticamente `ProductionJob.register_history(estado=...)`
  en dos momentos: al marcar un trabajo como **Impreso** (estado `IMPRESO`) y al
  **cancelarlo** (estado `CANCELADO`). Cubre todos los caminos: cambiar el estado
  del trabajo en `ProductionJobAdmin`, editarlo desde el inline de trabajos del
  Presupuesto (`PresupuestoAdmin.save_related`), y cancelar el pedido entero
  (`Presupuesto.cancel()` vía dropdown o acción masiva).
  Idempotente vía flag `history_added`; se resetea en `mark_obsolete()` para que
  la reimpresión vuelva a registrarse. Guarda copia de `titulo` (de
  `history_title()`), `cantidad`, `horas_impresion`, `estado` y `finalizado_el`,
  más FKs a `maquina` (CASCADE) y `presupuesto` (SET_NULL), así el historial
  sobrevive a cambios/borrados del trabajo. No registra trabajos sin máquina
  asignada. Se muestra como inline de solo lectura (`HistorialImpresionInline`)
  dentro de cada Máquina, ordenado por fecha descendente.
- Proxies de admin: **ColaProduccion** (tablero por máquina) y **Tablero**
  (panel general: qué se imprime, próximas entregas, qué comprar).

### gastos
Gastos **operativos / de estructura** del negocio (NO costos de producción ni
compras de insumos, que van por inventory). Sirve para el resultado operativo.
- **Gasto**: una erogación real con `fecha`. `categoria` (Administración /
  Comercialización / Suscripciones / IT / Otro), `concepto`, `monto`, `proveedor`,
  `medio_pago`, `es_recurrente` + `periodicidad` (Único/Mensual/Anual), `notas`.
  Prop `monthly_equivalent` (Mensual→monto, Anual→monto/12, no recurrente→0) para
  el run-rate. Se carga un Gasto por cada pago real (modelo basado en eventos).
- **TopeGasto**: tope (presupuesto) mensual por categoría (única por categoría).
- Proxy de admin: **PanelGastos** ("Panel de gastos"), página de solo lectura con
  **filtro por mes y año** (selector; mes 0 = todo el año). Muestra: total y
  desglose por categoría (tabla + torta), evolución mensual (barras), comparativo
  vs período anterior (variación %), compromiso mensual recurrente (run-rate +
  proyección anual), **resultado operativo** (ventas aprobadas del período −
  gastos, y gastos/ventas %), control de **topes** por categoría, y acumulado
  anual + promedio mensual. Export a Excel (openpyxl, 5 hojas). Lógica en
  `gastos/metrics.py`; ventas vienen de `Presupuesto.total` por `approved_at`.

## Conceptos clave / reglas de negocio

- **El dinero es @property, no columna.** `Presupuesto.total`,
  `PresupuestoItem.line_total`, `Producto.unit_cost/unit_price` se calculan en
  Python. **No se pueden agregar con el ORM** (`Sum('total')` NO existe). Para
  sumar montos hay que iterar en Python (con `prefetch_related` para evitar
  N+1). A la escala del negocio es exacto y rápido.
- **Piezas y stock (descuento AL APROBAR).** `Presupuesto._provision_production()`
  corre al aprobar: por cada pieza necesaria (`units_needed × cantidad`) descuenta
  primero del **stock de piezas** (esas NO se imprimen), encola solo lo que falta
  imprimir (un `ProductionJob` **por pieza**, respetando AMS) y descuenta su
  **filamento** ahí mismo (`consume_stock`, flag `stock_consumed`). Los
  **agregados** se descuentan a nivel producto. Así el consumo impacta de
  inmediato en las métricas de inventario/costos, sin esperar a imprimir.
  Idempotente vía `Presupuesto.stock_provisioned`.
- **El consumo de producción puede dejar el stock NEGATIVO.** `deduct_stock`
  acepta `allow_negative` (default False = nunca negativo, para ajustes/compras).
  La producción lo llama con `allow_negative=True`: si aprobás más de lo que
  tenés, el stock queda en negativo y se registra el consumo completo en el
  ledger. Así el faltante queda visible de forma persistente (lo levanta el
  pronóstico de compra) y la reversa por cancelación devuelve lo justo.
- **Pronóstico de compra POST-aprobación** (`scheduler.material_forecast`): como
  el material ya se descontó al aprobar, el pronóstico NO proyecta consumo
  futuro: mira el **stock real** y lista todo lo que quedó **por debajo de su
  mínimo** (o en negativo). `shortfall = min_stock − stock`; `runs_out_at` = el
  primer trabajo en cola que usa ese insumo (cuándo hay que tenerlo en la
  máquina). Lo muestra el Tablero en "Comprar materia prima".
- **Reversa al cancelar** (`Presupuesto.cancel()`, idempotente vía
  `stock_reversed`): al cancelar un pedido **Aprobado/En producción** devuelve el
  inventario: filamento de los trabajos **no impresos** (los impresos ya lo
  usaron), las **piezas tomadas del stock**, las **piezas ya impresas** (pasan al
  stock de piezas) y los **agregados** completos. Cancela los trabajos en cola.
  Movimientos con motivo `BUDGET_CANCELLED`. No revierte desde COMPLETADO.
  Se dispara desde el dropdown (`apply_status_change`) o la acción masiva
  "Cancelar y devolver el inventario".
- **Pedido para stock (sin cliente).** `Presupuesto.para_stock=True` marca un
  pedido de reposición de stock interno (no para un cliente). Se aprueba,
  imprime y completa igual que cualquier pedido; la diferencia es al COMPLETAR:
  `add_finished_to_stock()` suma cada producto terminado a
  `Producto.stock_quantity` (idempotente vía `finished_stock_added`). Se dispara
  al llegar a COMPLETADO por `sync_status_from_jobs` o por el dropdown
  (`apply_status_change`). Si `client_name` queda vacío, el admin lo completa con
  "Reposición de stock". Un pedido para stock NUNCA consume su propio stock de
  terminados: siempre produce.
- **Pedido de cliente consume stock de terminados primero.** Al aprobar un
  pedido de CLIENTE (no `para_stock`), `_provision_production` sirve cada línea
  primero del `Producto.stock_quantity` (ya armado): lo servido NO se produce ni
  consume piezas/material/agregados. La cantidad servida se guarda en
  `PresupuestoItem.from_finished_stock`; el resto (`produce_qty = quantity −
  from_finished_stock`) sigue el flujo normal (stock de piezas → imprimir, +
  agregados). Orden de consumo: stock de terminados → stock de piezas → imprimir.
  Al cancelar, `cancel()` devuelve al `Producto.stock_quantity` lo servido
  (`from_finished_stock`, que luego se resetea a 0) y reconstruye piezas/agregados
  a devolver sobre `produced = quantity − from_finished_stock`.
- **Pedido listo para entregar (sin producción).** Si un pedido de cliente se
  sirve 100% del stock (de terminados o de piezas) y no genera ningún trabajo,
  `Presupuesto.is_ready_to_deliver` es True (APROBADO + `stock_provisioned` + sin
  jobs). El admin lo muestra con la columna «Sin producción» (✓ Listo para
  entregar), avisa al aprobar que no genera producción, y la acción masiva
  «Marcar como entregado» lo pasa a COMPLETADO.
- **Impresión obsoleta (reimprimir).** Si una impresión sale mal,
  `ProductionJob.mark_obsolete(scrap_grams)` la devuelve a la cola (PENDING) para
  reimprimirse. El filamento ya se había descontado al aprobar; de ese total,
  `scrap_grams` (lo que se gastó en la impresión fallida, cargado a mano) se
  PIERDE y la diferencia (total de la pieza − scrap, prorrateado por línea de
  filamento si es multicolor) vuelve al stock (movimiento `REPRINT_FAILURE`). El
  trabajo queda `stock_consumed=False`, así la reimpresión vuelve a descontar el
  total al marcarse Impresa: neto = total + scrap. Los **agregados NO se tocan**
  (se usan al armar la pieza ya impresa, y la pieza se reimprime igual). Se opera
  desde la acción del admin «Marcar impresión obsoleta (reimprimir)», que muestra
  un paso intermedio para cargar los gramos perdidos por trabajo.
- **Sobrante de gcode → stock.** Si una corrida saca de más
  (`pieces_per_gcode`), al marcar el trabajo **Impreso** la sobrante se suma a
  `Pieza.stock_quantity` (`ProductionJob.register_surplus()`, flag
  `surplus_added`). `finished_at` se setea al pasar a DONE (lo usan las métricas
  de producción).
- **Multicolor**: un `Producto.is_multicolor=True` solo se asigna a máquinas con
  `supports_multicolor=True`. El scheduler lo respeta y `ProductionJob.clean()`
  rechaza asignaciones inválidas.
- **Scheduler** (`production/scheduler.py`): cada máquina activa procesa su cola
  en orden. Ventana de carga 07:00–23:00 (un trabajo solo *arranca* dentro de la
  ventana; una vez arrancado imprime de corrido, puede cruzar la noche).
  Asignación balanceada greedy (la máquina que se libera antes).
  `persist_schedule()` guarda el snapshot de tiempos en cada job.
- **Métricas** (`budgets/metrics.py`): KPIs de ventas/producción/inventario por
  semana/mes/año. Ventas se miden por `approved_at`, producción por
  `finished_at`, compras por `confirmed_at`. Página en admin → Métricas, con
  Chart.js y botón de export a Excel (openpyxl, 5 hojas).

## Admin: patrones usados

- **Modelos proxy** para páginas de solo lectura (`changelist_view` +
  `TemplateResponse` + `self.admin_site.each_context(request)` +
  `change_list_template` propio). Permisos `has_*_permission` en False.
- Templates de admin custom heredan de `admin/base_site.html`. Estilo del
  negocio: **blanco y negro** (fondo `#111`, texto blanco; nada de colores de
  marca salvo alertas en rojo).
- `templates/admin/base_site.html` agrega la **campanita de bajo stock** (via el
  context processor `inventory.context_processors.low_stock_alerts`).
- `list_editable` en el changelist llama `save_model`/`save_related` **una vez
  por fila** — ojo al recalcular cosas pesadas (ej. en MaquinaAdmin se difiere
  `persist_schedule` con un flag `request._needs_reschedule`).
- **Selector de idioma (i18n) en la navbar.** `base_site.html` agrega un
  `<select>` que postea a la vista `set_language` (`/i18n/setlang/`, ruteada en
  `config/urls.py`). `LocaleMiddleware` (entre Session y Common) detecta el idioma
  por cookie/sesión. `LANGUAGES = [('es', ...), ('en', ...)]`, `LOCALE_PATHS =
  [BASE_DIR/'locale']`, y el context processor `i18n` en TEMPLATES. El "chrome"
  del admin de Django (Add/Save/Home/...) ya trae traducciones es/en
  precompiladas. **Todas nuestras cadenas propias** (verbose_names, labels,
  help_texts, choices, descripciones de acciones, mensajes del admin y textos de
  los templates custom) están marcadas con `gettext`/`gettext_lazy` (Python) y
  `{% translate %}`/`{% blocktranslate %}` (templates), y se traducen al inglés
  vía `locale/en/LC_MESSAGES/django.mo`. El PDF del cliente queda en español a
  propósito. ⚠️ **gettext NO está instalado** (`xgettext`/`msgfmt` faltan), así
  que `makemessages`/`compilemessages` no corren: el `.mo` se genera con
  `python scripts/compile_messages.py` (compilador MO en Python puro; el catálogo
  vive en ese script como un dict por app que se fusiona). Para sumar/editar una
  traducción: marcala con gettext en el código, agregá el par ES→EN al sub-dict
  correspondiente y corré el script.
  - **Trampas al traducir** (ya resueltas, repetir si se agregan cadenas):
    (1) los f-strings no se pueden extraer → convertir a `gettext("… %(x)s …") %
    {"x": x}` con placeholders nombrados; (2) `gettext_lazy` devuelve un proxy que
    rompe `json.dumps` y `openpyxl` → envolver con `str(...)` en esos bordes;
    (3) en `{% blocktranslate %}` el msgid real usa `%(var)s` y **duplica el `%`
    literal** (`%%`); (4) no usar `\"` dentro de `{% translate "…" %}` (el
    templatizador lo rompe) → usar comillas simples en el tag: `{% translate '…"…"…' %}`.
    Para validar que los msgids de los templates están todos en el catálogo se
    puede extraer con `django.utils.translation.template.templatize`.

## Gotchas

- Activar el venv falla si no hacés `cd` al proyecto primero.
- `ManifestStaticFilesStorage`: cualquier `.js`/`.css` nuevo necesita
  `collectstatic`. Si un JS minificado trae `//# sourceMappingURL=...` apuntando
  a un `.map` inexistente, el post-procesado **falla** — hay que quitar ese
  comentario o incluir el `.map`. (Pasó con Chart.js.)
- `Filament` no tiene `name`. Usar `brand`/`material_type`/`color`.
- xhtml2pdf fragmenta los fondos de bloques con hijos que tienen padding/margin
  o `<ul>/<li>`; usar contenido inline (spans + `<br/>`) para fondos continuos.
- `USE_TZ=True`, zona `America/Argentina/Cordoba`. Para cálculos por día/semana
  usar `timezone.localtime` + `make_aware`.

## Convenciones

- Código, comentarios y textos del admin en **español**. Mensajes claros para
  alguien no técnico.
- No commitear sin que lo pidan. Crear commits nuevos (no `--amend`).
- Hay un agente `review` para verificar cambios end-to-end después de implementar.
- API REST: solo inventario (`/api/filaments`, `/api/aggregates`,
  `/api/stock-movements`). El resto del flujo va por el admin.
