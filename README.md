# Sistema de presupuestos para impresión 3D — Backend

Backend en Django + Django REST Framework. Resuelve el cálculo de costo real
de material en piezas multicolor/multimaterial: en vez de multiplicar el
peso total por el filamento más caro, cada filamento usado en una pieza se
carga como una línea independiente (filamento + gramos), y el costo total
de material es la suma exacta de cada línea.

## ¿Qué incluye?

- **Inventario** (`inventory`): filamentos (marca, material, color, precio,
  stock en gramos) y agregados (argollas, packaging, llaveros, etc. con
  precio y stock).
- **Presupuestos** (`budgets`): cada presupuesto tiene líneas de filamento
  usado y líneas de agregados usados. Calcula automáticamente: costo de
  material, costo de agregados, costo de máquina (tiempo × tarifa/hora),
  subtotal y total con margen.
- **Aprobación de presupuestos**: al aprobar, descuenta stock automáticamente
  (sin permitir que quede negativo) y deja registro de todos los movimientos.
- **Panel de administración**: para cargar y editar todo sin necesidad de
  programar nada (`/admin/`).
- **API REST**: para que el frontend en Next.js consuma todo (`/api/`).

## Cómo correrlo (primera vez)

Vas a necesitar tener Python instalado (versión 3.10 o superior).

1. Abrí una terminal en esta carpeta (`presupuestos3d`).
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

4. Creá tu usuario administrador (te va a pedir usuario, email y contraseña):

   ```bash
   python manage.py createsuperuser
   ```

   *(Ya hay uno de prueba creado: usuario `admin`, contraseña `admin1234`.
   Te recomiendo cambiarla o crear uno nuevo antes de usarlo en serio.)*

5. Levantá el servidor:

   ```bash
   python manage.py runserver
   ```

6. Entrá a `http://localhost:8000/admin/` con tu usuario y contraseña.
   Ahí podés cargar filamentos, agregados y presupuestos directamente,
   sin necesidad de tener el frontend listo todavía.

## Cómo cargar datos en el panel de administración

1. **Filamentos**: cargá cada combinación de marca + material + color que
   uses, con su precio por kg y el stock que tenés en gramos.
2. **Agregados**: cargá cada insumo (argollas, packaging, llaveros, etc.)
   con su precio por unidad y stock.
3. **Presupuestos**: creá un presupuesto, agregale las líneas de filamento
   (una por cada color/material que entra en la pieza, con los gramos que
   usa cada uno) y las líneas de agregados que correspondan. El sistema
   calcula todo solo, lo ves en el resumen de costos.
4. Para aprobar un presupuesto y descontar stock automáticamente: seleccionalo
   en la lista y usá la acción "Aprobar presupuestos seleccionados".

## Estructura de la API (para cuando conectemos Next.js)

- `GET/POST /api/filaments/` — listar/crear filamentos
- `GET/POST /api/aggregates/` — listar/crear agregados
- `GET /api/stock-movements/` — historial de movimientos de stock
- `GET/POST /api/budgets/` — listar/crear presupuestos (con sus líneas)
- `GET /api/budgets/{id}/check-stock/` — ver si faltan materiales, sin aprobar
- `POST /api/budgets/{id}/approve/` — aprobar y descontar stock

## Próximo paso

Este backend ya está probado y funcionando. El siguiente paso es construir
las pantallas en Next.js que consuman esta API: inventario, formulario de
presupuesto con cálculo en vivo, y listado de presupuestos.
