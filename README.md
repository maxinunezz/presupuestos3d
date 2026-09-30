# 3darg — Backend de gestión

Backend en **Django** para **3darg**, un negocio de impresión 3D multi-marca en
Argentina. Centraliza el costeo de productos, los presupuestos a clientes, el
inventario de filamentos y agregados, las compras de insumos, la cola de
producción de las impresoras, los gastos operativos y un panel de métricas del
negocio.

Casi todo se opera desde el **Django admin** (no hace falta un frontend para
usarlo día a día). La API REST solo expone recursos de inventario, pensada
para un futuro frontend en Next.js.

## Qué resuelve

- **Costeo real de productos multicolor/multimaterial**: cada producto se
  arma con piezas, y cada pieza puede usar varios filamentos a la vez (una
  línea por filamento + gramos usados). El costo de material es la suma
  exacta de cada línea, no un promedio.
- **Presupuestos a clientes** con PDF, estados (borrador → enviado →
  aprobado → en producción → completado) y fechas de seguimiento.
- **Descuento de inventario automático al aprobar** un presupuesto: primero
  contra stock de productos/piezas ya armados, después contra stock de
  materia prima, encolando en producción solo lo que falta imprimir.
- **Cola de producción por máquina**, con planificación automática de
  horarios, reimpresión ante fallas y registro de sobrantes al stock.
- **Compras de insumos**, con actualización de precios y stock al confirmar.
- **Gastos operativos y de estructura** del negocio (separados de los costos
  de producción), con topes por categoría y resultado operativo.
- **Panel de métricas** con ventas, producción, costos, valor de inventario y
  reparto de ganancias entre socios — exportable a Excel y PDF.

## Stack

- Python 3.12, Django 6.0.6, Django REST Framework 3.17.
- SQLite en local (`db.sqlite3`); Postgres/Neon en producción vía
  `DATABASE_URL`.
- whitenoise para estáticos, xhtml2pdf para los PDF (presupuestos y
  reportes), openpyxl para exportar a Excel, Chart.js para los gráficos del
  admin (servido local, sin CDN).
- Deploy en Vercel (serverless).

## Apps

```
config/          # proyecto Django (settings, urls, dashboard del admin)
inventory/        # filamentos, agregados, stock, movimientos, compras
budgets/           # costeo de productos, presupuestos, PDF al cliente, métricas
production/        # máquinas, cola de impresión, scheduler, tableros
gastos/             # gastos operativos/estructura, topes, panel de gastos
templates/         # templates a nivel proyecto (override del admin)
scripts/           # generar_instructivo.py: manual en PDF del panel de administración
```

Un recorrido más detallado de cada modelo y las reglas de negocio está en
`CLAUDE.md` (pensado como contexto interno de desarrollo, no se publica).

## Cómo correrlo (primera vez)

Necesitás Python 3.12 (o similar).

1. Cloná el repo y entrá a la carpeta.
2. Creá un entorno virtual e instalá las dependencias:

   ```bash
   python3 -m venv venv
   source venv/bin/activate        # en Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Aplicá las migraciones (crea la base de datos):

   ```bash
   python manage.py migrate
   ```

4. Creá tu usuario administrador:

   ```bash
   python manage.py createsuperuser
   ```

5. Levantá el servidor:

   ```bash
   python manage.py runserver
   ```

6. Entrá a `http://localhost:8000/admin/` con tu usuario y contraseña. Desde
   ahí se opera todo: inventario, productos, presupuestos, producción, gastos
   y métricas.

Para generar el manual en PDF del panel de administración (pensado para el
dueño del negocio, no técnico):

```bash
python scripts/generar_instructivo.py
```

## API REST

Solo expone inventario, para un futuro frontend en Next.js. El resto del
flujo (presupuestos, producción, gastos, métricas) se opera por el admin.

- `GET/POST /api/filaments/` — listar/crear filamentos
- `GET/POST /api/aggregates/` — listar/crear agregados
- `GET /api/stock-movements/` — historial de movimientos de stock

## Idioma

El panel tiene selector de idioma (Español / English) en la navbar. El PDF
que se le manda al cliente siempre queda en español.
