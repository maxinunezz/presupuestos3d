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
- **Producto**: costeo de UNA pieza. Materiales (líneas de filamento/agregado),
  máquina, mano de obra, merma, margen. `is_multicolor` (solo imprime en
  máquinas con AMS). Props de costo: `material_cost`, `aggregate_cost`,
  `machine_cost`, `labor_cost`, `unit_cost`, `unit_price`.
- **ProductoFilamentLine** (`grams_used`, `unit_cost` congelado) /
  **ProductoAggregateLine** (`quantity`, `unit_cost` congelado).
- **Presupuesto**: cotización a un cliente. `status` (DRAFT/SENT/APPROVED/
  IN_PRODUCTION/COMPLETED/CANCELLED), `fixed_cost`, fechas por estado
  (`sent_at`, `approved_at`, `production_started_at`, `production_finished_at`,
  `completed_at`), `due_date`. Props: `items_total`, `subtotal`, `total`,
  `total_pieces`, `total_print_hours`, `estimated_delivery`.
  - `approve()`: pasa a APPROVED, setea `approved_at`, genera trabajos de
    producción y calcula entrega. **NO descuenta stock al aprobar** (se descuenta
    al imprimir cada trabajo).
- **PresupuestoItem**: línea de presupuesto. `quantity`, `unit_price` (congelado
  al guardar). Props: `effective_unit_price`, `line_total`.
- Proxy de admin: **Metricas** (panel de KPIs, ver abajo).

### production
- **Maquina**: una impresora. `is_active`, `supports_multicolor` (la Bambu Lab
  con AMS sí, la Ender no).
- **ProductionJob**: un producto de un presupuesto en la cola de una máquina.
  `status` (PENDING/PRINTING/DONE/CANCELLED), `order`, `machine`,
  `estimated_start/estimated_print_end` (snapshot del scheduler), `started_at`,
  `finished_at`, `stock_consumed`. Props: `print_hours`, `post_hours`,
  `is_open`, `requires_multicolor`. `consume_stock()` descuenta material
  (idempotente) cuando el trabajo se marca Impreso.
- Proxies de admin: **ColaProduccion** (tablero por máquina) y **Tablero**
  (panel general: qué se imprime, próximas entregas, qué comprar).

## Conceptos clave / reglas de negocio

- **El dinero es @property, no columna.** `Presupuesto.total`,
  `PresupuestoItem.line_total`, `Producto.unit_cost/unit_price` se calculan en
  Python. **No se pueden agregar con el ORM** (`Sum('total')` NO existe). Para
  sumar montos hay que iterar en Python (con `prefetch_related` para evitar
  N+1). A la escala del negocio es exacto y rápido.
- **Stock se descuenta al IMPRIMIR, no al aprobar.** Aprobar un presupuesto solo
  genera la cola. `ProductionJob.consume_stock()` descuenta cuando el trabajo
  pasa a DONE (flag `stock_consumed`, idempotente).
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
