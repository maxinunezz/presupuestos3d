#!/usr/bin/env python3
"""Compila los catálogos de traducción del proyecto SIN GNU gettext.

En este entorno no están instaladas las herramientas de línea de comandos de
gettext (`xgettext`/`msgfmt`), así que `manage.py makemessages` /
`compilemessages` no funcionan. Este script genera a mano los archivos
`locale/<lang>/LC_MESSAGES/django.po` (legible) y `django.mo` (binario que lee
Django vía el módulo `gettext` de la stdlib).

Sólo traducimos nuestras cadenas propias marcadas con gettext (sobre todo los
nombres de las apps que se ven en el índice del admin). El "chrome" del admin de
Django (Add/Change/Save/Home/etc.) ya viene con sus traducciones es/en
precompiladas, así que el selector de idioma de la navbar las cambia solo.

Para agregar/editar traducciones: editá el dict TRANSLATIONS y corré:

    python scripts/compile_messages.py
"""
from __future__ import annotations

import array
import struct
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Traducciones de nuestras cadenas propias. La clave es la cadena fuente en
# español (tal cual se pasa a gettext); el valor, su traducción al inglés.
# Las cadenas que no figuran acá quedan como están (en español) — incluyendo
# "Español"/"English", que a propósito se muestran siempre en su propio idioma.
#
# Se mantiene un sub-dict por app (más fácil de mantener) y se fusionan al final.
# Si una misma cadena fuente aparece en dos apps con distinta traducción, gana la
# del merge final / los OVERRIDES de abajo (un msgid = una sola traducción).

COMMON = {
    'Idioma': 'Language',
    'Inventario': 'Inventory',
    'Presupuestos': 'Budgets',
    'Producción': 'Production',
    'Gastos': 'Expenses',
}

INVENTORY = {
    'Otro': 'Other',
    'Marca': 'Brand',
    'Tipo de material': 'Material type',
    'Color': 'Color',
    'Color (hex)': 'Color (hex)',
    'Ej: #FF0000. Opcional, para mostrar una muestra de color en el front.':
        'E.g. #FF0000. Optional, to show a color swatch on the front end.',
    'Costo por kg': 'Cost per kg',
    'Stock disponible (g)': 'Available stock (g)',
    'Stock mínimo (g)': 'Minimum stock (g)',
    'Si el stock baja de este valor, salta la alerta de bajo stock '
    '(la campanita). En gramos. Ej: 1000 = 1 kg. Poné 0 para no avisar '
    'de este filamento.':
        'If stock drops below this value, the low-stock alert (the bell) is '
        'triggered. In grams. E.g. 1000 = 1 kg. Set 0 to disable alerts for '
        'this filament.',
    'Activo': 'Active',
    'Creado': 'Created',
    'Actualizado': 'Updated',
    'Filamento': 'Filament',
    'Filamentos': 'Filaments',
    'Herraje (argollas, llaveros, etc.)': 'Hardware (rings, keychains, etc.)',
    'Decoración (pegatinas, etc.)': 'Decoration (stickers, etc.)',
    'Unidad': 'Unit',
    'Par': 'Pair',
    'Metro': 'Meter',
    'Gramo': 'Gram',
    'Nombre': 'Name',
    'Categoría': 'Category',
    'Costo por unidad': 'Cost per unit',
    'Stock disponible': 'Available stock',
    'Stock mínimo': 'Minimum stock',
    'Si el stock baja de este valor, salta la alerta de bajo stock '
    '(la campanita). Va en la MISMA unidad del agregado: si se mide en '
    'unidades, poné unidades (ej: pelotas → 20); si se mide en gramos, '
    'poné gramos (ej: argollas → 200). Poné 0 para no avisar de este agregado.':
        'If stock drops below this value, the low-stock alert (the bell) is '
        'triggered. Use the SAME unit as the aggregate: if it is measured in '
        'units, enter units (e.g. balls → 20); if it is measured in grams, '
        'enter grams (e.g. rings → 200). Set 0 to disable alerts for this aggregate.',
    'Agregado': 'Aggregate',
    'Agregados': 'Aggregates',
    'Totales de inventario': 'Inventory totals',
    'Compra': 'Purchase',
    'Presupuesto aprobado': 'Budget approved',
    'Producción (impresión)': 'Production (printing)',
    'Cancelación de presupuesto': 'Budget cancellation',
    'Ajuste manual': 'Manual adjustment',
    'Reimpresión por falla': 'Reprint due to failure',
    'Cantidad': 'Quantity',
    'Negativo = salida de stock. Positivo = entrada de stock.':
        'Negative = stock out. Positive = stock in.',
    'Motivo': 'Reason',
    'Presupuesto relacionado': 'Related budget',
    'Nota': 'Note',
    'Fecha': 'Date',
    'Movimiento de stock': 'Stock movement',
    'Movimientos de stock': 'Stock movements',
    'Un movimiento de stock debe estar vinculado a exactamente '
    'un Filamento o un Agregado (no ambos, no ninguno).':
        'A stock movement must be linked to exactly one Filament or one '
        'Aggregate (not both, not neither).',
    'Ajuste manual de stock': 'Manual stock adjustment',
    'Ajustes manuales de stock': 'Manual stock adjustments',
    'Borrador': 'Draft',
    'Confirmada': 'Confirmed',
    'Proveedor': 'Supplier',
    'N° de factura / remito': 'Invoice / delivery note no.',
    'Notas': 'Notes',
    'Estado': 'Status',
    'Confirmada el': 'Confirmed at',
    'Creada': 'Created',
    'Actualizada': 'Updated',
    'Compras': 'Purchases',
    'sin proveedor': 'no supplier',
    "No se puede confirmar la compra #%(pk)s: su estado es "
    "'%(status)s'. Solo se pueden confirmar "
    "compras en estado Borrador.":
        "Purchase #%(pk)s cannot be confirmed: its status is '%(status)s'. "
        "Only purchases in Draft status can be confirmed.",
    'Cantidad comprada': 'Quantity purchased',
    'Filamento: en gramos. Agregado: en unidades.':
        'Filament: in grams. Aggregate: in units.',
    'Precio pagado': 'Price paid',
    'Filamento: costo por kg. Agregado: costo por unidad. Si se deja '
    'vacío, se mantiene el precio actual del artículo.':
        "Filament: cost per kg. Aggregate: cost per unit. If left blank, the "
        "item's current price is kept.",
    'Línea de compra': 'Purchase line',
    'Líneas de compra': 'Purchase lines',
    'Una línea de compra debe estar vinculada a exactamente un '
    'Filamento o un Agregado (no ambos, no ninguno).':
        'A purchase line must be linked to exactly one Filament or one '
        'Aggregate (not both, not neither).',
    'Bajo stock': 'Low stock',
    'Sí (por debajo del mínimo)': 'Yes (below minimum)',
    'No (stock OK)': 'No (stock OK)',
    'Bajo': 'Low',
    'OK': 'OK',
    'Costo/g': 'Cost/g',
    'Estado stock': 'Stock status',
    'Costo de línea': 'Line cost',
    'Resumen': 'Summary',
    'Compra #%(pk)s confirmada: stock y precios actualizados.':
        'Purchase #%(pk)s confirmed: stock and prices updated.',
    'Total': 'Total',
    'Resumen de la compra': 'Purchase summary',
    'Guardá la compra y agregá líneas para ver el total.':
        'Save the purchase and add lines to see the total.',
    '<br><i>Borrador: todavía no impactó el inventario. Usá la acción '
    '“Confirmar compra” en la lista para sumar el stock.</i>':
        '<br><i>Draft: it has not affected inventory yet. Use the '
        '“Confirm purchase” action in the list to add the stock.</i>',
    '(sin líneas todavía)': '(no lines yet)',
    'TOTAL': 'TOTAL',
    'Confirmar compra(s) seleccionadas (suma stock)':
        'Confirm selected purchase(s) (adds stock)',
    'Ítem': 'Item',
    'Origen': 'Source',
    'Stock luego del ajuste': 'Stock after adjustment',
    'Elegí el filamento a ajustar (o un agregado, no ambos).':
        'Choose the filament to adjust (or an aggregate, not both).',
    'Elegí el agregado a ajustar (o un filamento, no ambos).':
        'Choose the aggregate to adjust (or a filament, not both).',
    'Cantidad a ajustar. POSITIVO suma al stock, NEGATIVO resta. '
    'Filamento en gramos, agregado en unidades. '
    'Ej: 500 agrega 500 g; -200 quita 200 g. '
    'Si querés dejar el stock en un valor exacto, fijate cuánto hay '
    'hoy y poné la diferencia.':
        'Quantity to adjust. POSITIVE adds to stock, NEGATIVE subtracts. '
        'Filament in grams, aggregate in units. '
        'E.g. 500 adds 500 g; -200 removes 200 g. '
        'If you want to set stock to an exact value, check how much there is '
        'today and enter the difference.',
    'Motivo del ajuste (ej: conteo físico, rotura, sobrante de '
    'impresión, carga inicial de stock).':
        'Reason for the adjustment (e.g. physical count, breakage, print '
        'surplus, initial stock load).',
    'Elegí exactamente un artículo: un Filamento o un Agregado '
    '(no ambos, no ninguno).':
        'Choose exactly one item: a Filament or an Aggregate (not both, not neither).',
    'Ingresá una cantidad distinta de cero.': 'Enter a non-zero quantity.',
    'Stock ajustado en %(delta)s%(unidad)s. Nuevo stock: %(resultante)s %(unidad)s.':
        'Stock adjusted by %(delta)s%(unidad)s. New stock: %(resultante)s %(unidad)s.',
    'Buscar marca, color, material o agregado…':
        'Search brand, color, material or aggregate…',
    'Buscar': 'Search',
    'Limpiar': 'Clear',
    'Ver:': 'View:',
    'Todo': 'All',
    'Solo filamentos': 'Filaments only',
    'Solo agregados': 'Aggregates only',
    'Stock (g)': 'Stock (g)',
    'Stock (kg)': 'Stock (kg)',
    'Costo/kg': 'Cost/kg',
    'Valor en stock': 'Stock value',
    'Sin filamentos para esta búsqueda.': 'No filaments for this search.',
    'TOTAL filamentos': 'TOTAL filaments',
    'Costo/u': 'Cost/unit',
    'Sin agregados para esta búsqueda.': 'No aggregates for this search.',
    'TOTAL agregados': 'TOTAL aggregates',
    'Valor total de inventario': 'Total inventory value',
    '(filtrado)': '(filtered)',
}

GASTOS = {
    'Administración': 'Administration',
    'Comercialización': 'Marketing',
    'Suscripciones': 'Subscriptions',
    'IT': 'IT',
    'Único (no se repite)': 'One-time (does not repeat)',
    'Mensual': 'Monthly',
    'Anual': 'Annual',
    'Efectivo': 'Cash',
    'Transferencia': 'Bank transfer',
    'Tarjeta de crédito': 'Credit card',
    'Débito automático': 'Automatic debit',
    'Concepto': 'Concept',
    'Qué gasto es (ej: Contador, Google Workspace, Publicidad Instagram).':
        'What the expense is (e.g.: Accountant, Google Workspace, Instagram Ads).',
    'Monto ($)': 'Amount ($)',
    'Fecha del gasto. Define en qué mes/año cae en el panel.':
        'Expense date. Determines which month/year it falls under in the dashboard.',
    'Medio de pago': 'Payment method',
    'Es recurrente': 'Is recurring',
    'Marcalo si es un gasto fijo que se repite (suscripción, abono). '
    'Se usa para calcular el compromiso mensual (run-rate).':
        'Check it if it is a fixed expense that repeats (subscription, plan). '
        'Used to compute the monthly commitment (run-rate).',
    'Periodicidad': 'Frequency',
    'Si es recurrente, cada cuánto se paga (para el compromiso mensual).':
        'If recurring, how often it is paid (for the monthly commitment).',
    'Gasto': 'Expense',
    'Tope mensual ($)': 'Monthly cap ($)',
    'Gasto máximo esperado por mes para esta categoría. 0 = sin tope.':
        'Maximum expected spend per month for this category. 0 = no cap.',
    'Tope de gasto (presupuesto)': 'Expense cap (budget)',
    'Topes de gasto (presupuestos)': 'Expense caps (budgets)',
    'Tope %(categoria)s: $%(monto)s/mes': 'Cap %(categoria)s: $%(monto)s/month',
    'Panel de gastos': 'Expenses dashboard',
    'Pago': 'Payment',
    'Recurrencia': 'Recurrence',
    'Marcá los gastos fijos (suscripciones, abonos) para que entren '
    'en el compromiso mensual del panel.':
        'Mark fixed expenses (subscriptions, plans) so they count '
        'toward the monthly commitment in the dashboard.',
    'Monto': 'Amount',
    'Todo el año': 'Whole year',
    'Enero': 'January', 'Febrero': 'February', 'Marzo': 'March',
    'Abril': 'April', 'Mayo': 'May', 'Junio': 'June',
    'Julio': 'July', 'Agosto': 'August', 'Septiembre': 'September',
    'Octubre': 'October', 'Noviembre': 'November', 'Diciembre': 'December',
    'Año %(year)s': 'Year %(year)s',
    'Gastos 3darg — %(period)s (%(range)s)': '3darg Expenses — %(period)s (%(range)s)',
    'Total de gastos': 'Total expenses',
    'Cantidad de gastos': 'Number of expenses',
    'Total %(prev)s': 'Total %(prev)s',
    'Variación vs período anterior': 'Change vs previous period',
    'Compromiso mensual recurrente': 'Recurring monthly commitment',
    'Proyección anual recurrente': 'Recurring annual projection',
    'Ventas del período': 'Sales for the period',
    'Resultado operativo (ventas − gastos)': 'Operating result (sales − expenses)',
    'Gastos sobre ventas': 'Expenses over sales',
    'Acumulado año %(year)s': 'Year %(year)s cumulative',
    'Promedio mensual': 'Monthly average',
    'Por categoría': 'By category',
    '% del total': '% of total',
    'Evolución': 'Trend',
    'Mes': 'Month',
    'Gastos por mes — %(year)s': 'Expenses by month — %(year)s',
    'Recurrentes': 'Recurring',
    'Equivalente mensual': 'Monthly equivalent',
    'Topes': 'Caps',
    'Tope': 'Cap',
    '% usado': '% used',
    '¿Excedido?': 'Exceeded?',
    'Sí': 'Yes',
    'No': 'No',
    'Gastos operativos del negocio (administración, comercialización, suscripciones, IT)\n'
    '    por <b>fecha del gasto</b>. No incluye los <b>costos</b> de producción ni las compras de\n'
    '    insumos (eso va por Inventario y Métricas). El resultado operativo cruza estos gastos\n'
    '    con las ventas aprobadas del mismo período.':
        'Operating expenses of the business (administration, marketing, subscriptions, IT)\n'
        '    by <b>expense date</b>. It does not include production <b>costs</b> or supply\n'
        '    purchases (those go through Inventory and Metrics). The operating result crosses these\n'
        '    expenses with the approved sales of the same period.',
    'Ver': 'View',
    'Descargar Excel': 'Download Excel',
    'Acumulado del año': 'Year to date',
    'Gastos por categoría': 'Expenses by category',
    'Desglose por categoría': 'Breakdown by category',
    'Cant.': 'Qty.',
    'vs ant.': 'vs prev.',
    'Resultado operativo (vs ventas)': 'Operating result (vs sales)',
    'Ventas aprobadas': 'Approved sales',
    'Gastos del período': 'Expenses for the period',
    'Ventas − Gastos': 'Sales − Expenses',
    'Compromiso por mes': 'Commitment per month',
    'Proyección anual': 'Annual projection',
    'Gastos recurrentes del período': 'Recurring expenses for the period',
    'Equiv. mensual': 'Monthly equiv.',
    'No hay gastos recurrentes en este período.': 'There are no recurring expenses in this period.',
    'Control de topes por categoría': 'Cap control by category',
    'Restante': 'Remaining',
    'No hay topes cargados. Cargá topes mensuales en «Topes de gasto».':
        'No caps configured. Set monthly caps in «Expense caps».',
}

BUDGETS = {
    'Alta': 'High',
    'Media': 'Medium',
    'Baja': 'Low',
    'Sin prioridad': 'No priority',
    'Nombre / pieza': 'Name / part',
    'Descripción': 'Description',
    'Prioridad en la cola': 'Queue priority',
    "Define en qué orden entra a la cola de producción: prioridad más alta se imprime antes. 'Sin prioridad' va al final de la cola.": "Defines the order in which it enters the production queue: higher priority is printed first. 'No priority' goes to the end of the queue.",
    'Multicolor (AMS)': 'Multicolor (AMS)',
    'Marcá si la pieza usa varios colores/filamentos en simultáneo. Solo se puede imprimir en máquinas con AMS (ej. Bambu Lab), no en la Ender.': 'Check this if the part uses several colors/filaments at once. It can only be printed on AMS-equipped machines (e.g. Bambu Lab), not on the Ender.',
    'Costo de máquina por hora': 'Machine cost per hour',
    'Merma de material (%)': 'Material waste (%)',
    'Desperdicio de filamento por purga (multicolor), soportes y fallas. Se aplica sobre el costo y el consumo de material.': 'Filament wasted on purge (multicolor), supports and failures. Applied to material cost and consumption.',
    'Post-proceso por pieza (hs)': 'Post-processing per part (hrs)',
    'Tiempo de armado, lijado, pintado, pegado de agregados, etc.': 'Time for assembly, sanding, painting, gluing add-ons, etc.',
    'Costo de mano de obra por hora': 'Labor cost per hour',
    'Margen (%)': 'Margin (%)',
    'Redondear precio a múltiplo de': 'Round price to a multiple of',
    'Ej: 100 redondea el precio a la centena más cercana. 0 = sin redondeo.': 'E.g. 100 rounds the price to the nearest hundred. 0 = no rounding.',
    'G-code': 'G-code',
    'Pegá acá el g-code del laminador (opcional).': 'Paste the slicer g-code here (optional).',
    'Archivo .3mf / modelo': 'File .3mf / model',
    'Subí el .3mf o archivo del modelo (opcional, solo desarrollo local).': 'Upload the .3mf or model file (optional, local development only).',
    'Stock de productos terminados': 'Finished products stock',
    'Unidades de este producto ya terminadas (armadas) y disponibles para entregar sin imprimir. Sube al completar un pedido de stock.': 'Units of this product already finished (assembled) and available to deliver without printing. Increases when a stock order is completed.',
    'Stock mínimo de terminados': 'Minimum finished stock',
    "Cuántas unidades terminadas querés tener siempre en stock. Si el stock baja de este número, el producto aparece como 'a reponer'. 0 = no se controla el mínimo.": "How many finished units you always want in stock. If the stock falls below this number, the product appears as 'to restock'. 0 = minimum not tracked.",
    'Costeo de producto': 'Product costing',
    'Costeo de productos': 'Product costing',
    'Costo por unidad (congelado)': 'Cost per unit (frozen)',
    "Costo por unidad guardado al costear. Si se deja vacío, se toma el precio actual del agregado.": "Cost per unit saved at costing time. If left empty, the add-on's current price is used.",
    'Línea de agregado': 'Add-on line',
    'Líneas de agregado': 'Add-on lines',
    'Nombre de la pieza': 'Part name',
    'Unidades necesarias por producto': 'Units needed per product',
    'Cuántas unidades de esta pieza lleva UN producto.': 'How many units of this part ONE product requires.',
    'Piezas por corrida de gcode': 'Parts per gcode run',
    'Cuántas unidades de esta pieza salen en UNA impresión (un gcode).': 'How many units of this part come out of ONE print (a single gcode).',
    'Horas de máquina por corrida de gcode': 'Machine hours per gcode run',
    'Tiempo de impresión de UNA corrida del gcode (saca `piezas por gcode`).': 'Print time of ONE gcode run (yields `parts per gcode`).',
    'Necesita AMS (multicolor)': 'Requires AMS (multicolor)',
    'Se marca solo cuando la pieza usa más de una línea de filamento (multicolor): debe ir a una máquina con AMS. Podés forzarlo a mano.': 'Set automatically when the part uses more than one filament line (multicolor): it must go to an AMS machine. You can force it manually.',
    'Stock de piezas impresas': 'Printed parts stock',
    'Unidades de esta pieza ya impresas y disponibles en stock.': 'Units of this part already printed and available in stock.',
    'Orden': 'Order',
    'Pieza': 'Part',
    'Piezas': 'Parts',
    'Gramos usados (por corrida)': 'Grams used (per run)',
    'Costo por gramo (congelado)': 'Cost per gram (frozen)',
    "Costo por gramo guardado al momento de costear. Si se deja vacío, se toma el precio actual del filamento.": "Cost per gram saved at costing time. If left empty, the filament's current price is used.",
    'Línea de filamento (pieza)': 'Filament line (part)',
    'Líneas de filamento (pieza)': 'Filament lines (part)',
    'Stock de piezas': 'Parts stock',
    'Métrica': 'Metric',
    'Métricas': 'Metrics',
    'Enviado': 'Sent',
    'Aprobado': 'Approved',
    'En producción': 'In production',
    'Completado': 'Completed',
    'Cancelado': 'Cancelled',
    'Cliente': 'Client',
    'Notas / descripción': 'Notes / description',
    'Pedido para reponer stock (sin cliente)': 'Order to restock (no client)',
    'Marcá si es producción para tu stock interno, no para un cliente. Funciona igual que un pedido normal (se aprueba, se imprime, se completa); al completarse, las unidades terminadas suman al stock de productos terminados en vez de entregarse.': 'Check this if it is production for your internal stock, not for a client. It works like a normal order (it is approved, printed, completed); when completed, the finished units are added to the finished products stock instead of being delivered.',
    'Costo fijo por pedido': 'Fixed cost per order',
    'Costo que se cobra una sola vez por pedido (setup, envío, etc.).': 'Cost charged once per order (setup, shipping, etc.).',
    'Redondear total a múltiplo de': 'Round total to a multiple of',
    'Ej: 100 redondea el total a la centena más cercana. 0 = sin redondeo.': 'E.g. 100 rounds the total to the nearest hundred. 0 = no rounding.',
    'Enviado el': 'Sent on',
    'Aprobado el': 'Approved on',
    'Producción iniciada el': 'Production started on',
    'Producción terminada el': 'Production finished on',
    'Completado el': 'Completed on',
    'Fecha de entrega': 'Due date',
    'Se calcula sola con la cola de producción. Podés pisarla a mano: si la editás, queda fija y deja de recalcularse.': 'Calculated automatically from the production queue. You can override it manually: if you edit it, it stays fixed and stops recalculating.',
    'Entrega fijada a mano': 'Due date set manually',
    'Inventario descontado': 'Inventory deducted',
    'Se marca al aprobar: ya se descontaron las piezas de stock, el filamento y los agregados. Evita descontar dos veces.': 'Set on approval: parts from stock, filament and add-ons have already been deducted. Prevents double deduction.',
    'Inventario devuelto': 'Inventory returned',
    'Se marca al cancelar un pedido aprobado: ya se devolvió al stock el material de lo que no se imprimió, las piezas y los agregados. Evita devolver dos veces.': 'Set when an approved order is cancelled: the material of what was not printed, the parts and the add-ons have already been returned to stock. Prevents returning twice.',
    'Sumado al stock de terminados': 'Added to finished stock',
    "Se marca al completar un pedido 'para stock': ya se sumaron sus unidades terminadas al stock de productos. Evita sumar dos veces.": "Set when a 'for stock' order is completed: its finished units have already been added to the products stock. Prevents adding twice.",
    "No se puede aprobar el presupuesto #%(pk)s: su estado es '%(status)s'. Solo se pueden aprobar presupuestos en estado Borrador o Enviado.": "Budget #%(pk)s cannot be approved: its status is '%(status)s'. Only budgets in Draft or Sent status can be approved.",
    'Pedido #%(pk)s: %(producto)s ×%(qty)s': 'Order #%(pk)s: %(producto)s ×%(qty)s',
    ' (faltaron %(shortage)s: quedó en negativo)': ' (%(shortage)s missing: went negative)',
    'ya impresa': 'already printed',
    'estaba en stock': 'was in stock',
    'Cancelación pedido #%(pk)s: %(producto)s ×%(qty)s': 'Order cancellation #%(pk)s: %(producto)s ×%(qty)s',
    'Cancelación pedido #%(pk)s: %(name)s ×%(qty)s': 'Order cancellation #%(pk)s: %(name)s ×%(qty)s',
    'Cantidad de piezas': 'Number of parts',
    'Precio unitario (congelado)': 'Unit price (frozen)',
    'Precio por pieza guardado al armar el presupuesto. Si se deja vacío, se toma el precio actual del producto. Queda fijo aunque después cambie el costeo del producto.': "Price per part saved when building the budget. If left empty, the product's current price is used. It stays fixed even if the product costing changes later.",
    'Servido del stock de terminados': 'Served from finished stock',
    'Unidades de esta línea que se sirvieron del stock de productos terminados al aprobar (no se produjeron). Se usa para devolver el stock si el pedido se cancela.': 'Units of this line served from the finished products stock on approval (not produced). Used to return stock if the order is cancelled.',
    'Producto del presupuesto': 'Budget product',
    'Productos del presupuesto': 'Budget products',
    'Por producto (corridas · gramos · horas)': 'Per product (runs · grams · hours)',
    'Guardá para ver el cálculo.': 'Save to see the calculation.',
    '%(runs)s corrida/s · %(grams)s g · %(hours)s h': '%(runs)s run/s · %(grams)s g · %(hours)s h',
    'Costo de línea (por corrida)': 'Line cost (per run)',
    'Cálculo de la pieza': 'Part calculation',
    'Guardá la pieza y agregá su filamento para ver el cálculo.': 'Save the part and add its filament to see the calculation.',
    'Por UN producto:': 'Per ONE product:',
    'Corridas de gcode:': 'Gcode runs:',
    'Filamento:': 'Filament:',
    'Horas de máquina:': 'Machine hours:',
    'Costo de filamento:': 'Filament cost:',
    'Necesita AMS:': 'Requires AMS:',
    'Sin mínimo': 'No minimum',
    'Bajo mínimo': 'Below minimum',
    'Faltan para el mínimo': 'Needed to reach minimum',
    'Impresión y máquina': 'Printing and machine',
    'Mano de obra / post-proceso': 'Labor / post-processing',
    'Precio': 'Price',
    'Archivo del modelo': 'Model file',
    'Costo/pieza': 'Cost/part',
    'Precio/pieza': 'Price/part',
    'Resumen de costos': 'Cost summary',
    'Guardá el producto para ver el resumen de costos.': 'Save the product to see the cost summary.',
    'corrida/s': 'run/s',
    '(sin piezas todavía)': '(no parts yet)',
    'Piezas del producto:': 'Product parts:',
    'Total filamento:': 'Total filament:',
    'Total horas de máquina:': 'Total machine hours:',
    'Necesita AMS (multicolor):': 'Requires AMS (multicolor):',
    'Costos por producto:': 'Costs per product:',
    'Material:': 'Material:',
    'merma': 'waste',
    'Agregados:': 'Add-ons:',
    'Máquina:': 'Machine:',
    'Mano de obra:': 'Labor:',
    'Costo por producto:': 'Cost per product:',
    'Margen:': 'Margin:',
    'PRECIO DE VENTA:': 'SALE PRICE:',
    'Importe': 'Amount',
    'Horas impr.': 'Print hrs.',
    'Precio del pedido': 'Order price',
    'Producción y entrega': 'Production and delivery',
    'Fechas': 'Dates',
    'Documento': 'Document',
    'Presupuesto no encontrado.': 'Budget not found.',
    'PDF': 'PDF',
    'PDF cliente': 'Client PDF',
    'PDF para el cliente': 'PDF for the client',
    'Guardá el presupuesto para poder generar el PDF.': 'Save the budget to be able to generate the PDF.',
    'Descargar PDF para el cliente': 'Download PDF for the client',
    'Sin producción': 'No production',
    'Listo para entregar': 'Ready to deliver',
    'Total pedido': 'Order total',
    'Entrega': 'Delivery',
    'Guardá el presupuesto y aprobalo para generar la cola.': 'Save the budget and approve it to generate the queue.',
    'Todavía no hay trabajos de producción. Se generan al <b>aprobar</b> el presupuesto.': 'There are no production jobs yet. They are generated when you <b>approve</b> the budget.',
    '(sin máquina)': '(no machine)',
    '&nbsp;&nbsp;%(producto)s ×%(qty)s → <b>%(maquina)s</b> (%(hours)s h, fin impr. %(fin)s)<br>': '&nbsp;&nbsp;%(producto)s ×%(qty)s → <b>%(maquina)s</b> (%(hours)s h, print end %(fin)s)<br>',
    'Impresión total:': 'Total printing:',
    'Post-proceso total:': 'Total post-processing:',
    'ENTREGA ESTIMADA:': 'ESTIMATED DELIVERY:',
    'Reposición de stock': 'Stock replenishment',
    "Trabajo '%(job)s' impreso: %(surplus)s unidad(es) sobrante(s) se sumaron al stock de la pieza.": "Job '%(job)s' printed: %(surplus)s surplus unit(s) were added to the part stock.",
    "El pedido pasó a '%(status)s' según el estado de sus trabajos de producción.": "The order moved to '%(status)s' based on the status of its production jobs.",
    'Pedido de stock #%(pk)s completado: se sumó al stock de productos terminados — %(detalle)s.': 'Stock order #%(pk)s completed: added to the finished products stock — %(detalle)s.',
    'Resumen del presupuesto': 'Budget summary',
    'Guardá el presupuesto y agregá productos para ver el total.': 'Save the budget and add products to see the total.',
    'c/u': 'each',
    '(sin productos todavía)': '(no products yet)',
    '%(n)s pieza/s': '%(n)s part/s',
    'Productos:': 'Products:',
    'Costo fijo:': 'Fixed cost:',
    'Subtotal:': 'Subtotal:',
    'TOTAL:': 'TOTAL:',
    'Presupuesto #%(pk)s: se sirvieron del stock de productos terminados (no se vuelven a producir): %(detalle)s.': 'Budget #%(pk)s: served from the finished products stock (will not be produced again): %(detalle)s.',
    'Presupuesto #%(pk)s: se tomaron piezas del stock (no se vuelven a imprimir): %(detalle)s.': 'Budget #%(pk)s: parts were taken from stock (will not be printed again): %(detalle)s.',
    '%(item)s (faltaron %(missing)s)': '%(item)s (%(missing)s missing)',
    'Presupuesto #%(pk)s aprobado y en cola. Ojo: el stock no alcanzó al descontar: %(items)s. Quedó en cero y conviene reponer.': 'Budget #%(pk)s approved and queued. Note: stock was not enough when deducting: %(items)s. It reached zero and should be restocked.',
    'Presupuesto #%(pk)s aprobado y servido 100%% del stock: NO genera producción. Está LISTO PARA ENTREGAR — usá la acción «Marcar como entregado» para completarlo.': 'Budget #%(pk)s approved and served 100%% from stock: it does NOT generate production. It is READY TO DELIVER — use the «Mark as delivered» action to complete it.',
    'Presupuesto #%(pk)s aprobado: se descontó el inventario y se generó la cola de producción.': 'Budget #%(pk)s approved: inventory was deducted and the production queue was generated.',
    'Aprobar presupuesto(s) y generar cola de producción': 'Approve budget(s) and generate production queue',
    'Marcar como entregado (completar pedido listo de stock)': 'Mark as delivered (complete a stock-ready order)',
    'Presupuesto #%(pk)s: no está listo para entregar (o tiene producción pendiente, o no está aprobado/servido de stock). No se completó.': 'Budget #%(pk)s: not ready to deliver (either it has pending production, or it is not approved/served from stock). It was not completed.',
    'Presupuesto #%(pk)s marcado como entregado y completado.': 'Budget #%(pk)s marked as delivered and completed.',
    'Presupuesto #%(pk)s cancelado. No había inventario que devolver.': 'Budget #%(pk)s cancelled. There was no inventory to return.',
    '%(n)s trabajo(s) cancelado(s)': '%(n)s job(s) cancelled',
    'filamento devuelto: %(det)s': 'filament returned: %(det)s',
    'agregados devueltos: %(det)s': 'add-ons returned: %(det)s',
    'productos terminados devueltos: %(det)s': 'finished products returned: %(det)s',
    'piezas al stock: %(det)s': 'parts to stock: %(det)s',
    'Presupuesto #%(pk)s cancelado. Se revirtió el inventario — ': 'Budget #%(pk)s cancelled. Inventory was reverted — ',
    'Cancelar presupuesto(s) y devolver el inventario': 'Cancel budget(s) and return the inventory',
    'Presupuesto #%(pk)s ya estaba cancelado.': 'Budget #%(pk)s was already cancelled.',
    'Semana': 'Week',
    'Año': 'Year',
    'Sem ': 'Wk ',
    '%(n).1f días': '%(n).1f days',
    'Sin máquina': 'No machine',
    '%(conv)s/%(sent)s enviados': '%(conv)s/%(sent)s sent',
    'Métricas 3darg — %(period)s (%(range)s)': '3darg Metrics — %(period)s (%(range)s)',
    'VENTAS': 'SALES',
    'Facturación aprobada': 'Approved billing',
    'Presupuestos aprobados': 'Approved budgets',
    'Ticket promedio': 'Average ticket',
    'Enviados / Aprobados': 'Sent / Approved',
    'Tasa de conversión': 'Conversion rate',
    'Margen bruto': 'Gross margin',
    'Tiempo de ciclo (aprob.→entrega)': 'Cycle time (approval→delivery)',
    'PRODUCCIÓN': 'PRODUCTION',
    'Piezas impresas': 'Printed parts',
    'Horas impresas': 'Printed hours',
    'Reimpresiones por falla': 'Reprints due to failure',
    'Tasa de reimpresión': 'Reprint rate',
    'Cumplimiento de entrega': 'On-time delivery',
    'INVENTARIO / COSTOS': 'INVENTORY / COSTS',
    'Gasto en compras': 'Purchase spending',
    'N° de compras': 'No. of purchases',
    'Consumo de material ($)': 'Material consumption ($)',
    'Filamento consumido (g)': 'Filament consumed (g)',
    'Insumos bajo stock mínimo': 'Supplies below minimum stock',
    'Facturación': 'Billing',
    'Facturación por %(period)s': 'Billing per %(period)s',
    'Productos': 'Products',
    'Producto': 'Product',
    'Cantidad vendida': 'Quantity sold',
    'Clientes': 'Clients',
    'Máquina': 'Machine',
    'Trabajos': 'Jobs',
    'KPIs del negocio por período. Ventas por <b>fecha de aprobación</b>, producción por\n    <b>fin de impresión</b>, compras por <b>fecha de confirmación</b>. El embudo y el stock bajo\n    son una foto del estado actual (no dependen del período).': 'Business KPIs by period. Sales by <b>approval date</b>, production by\n    <b>print end</b>, purchases by <b>confirmation date</b>. The funnel and low stock\n    are a snapshot of the current state (independent of the period).',
    'Ventas': 'Sales',
    'Conversión': 'Conversion',
    'Tiempo de ciclo': 'Cycle time',
    'Embudo de estados (ahora)': 'Status funnel (now)',
    'Productos más vendidos (cantidad)': 'Best-selling products (quantity)',
    'Sin ventas en el período.': 'No sales in the period.',
    'Productos más vendidos ($)': 'Best-selling products ($)',
    'Top clientes ($)': 'Top clients ($)',
    'Horas impresas por máquina': 'Printed hours per machine',
    'Uso por máquina': 'Usage per machine',
    'Horas': 'Hours',
    'No se imprimió nada en el período.': 'Nothing was printed in the period.',
    'Inventario y costos': 'Inventory and costs',
    'Filamento consumido': 'Filament consumed',
}

PRODUCTION = {
    'Activa': 'Active',
    'Si está inactiva, no se le asignan trabajos nuevos ni cuenta para la cola.':
        'If inactive, no new jobs are assigned to it and it does not count toward the queue.',
    'Imprime multicolor (AMS)': 'Prints multicolor (AMS)',
    'Marcá si la máquina puede imprimir piezas de varios colores en '
    'simultáneo (ej. Bambu Lab con AMS). La Ender no lo soporta.':
        'Check this if the machine can print parts in several colors at once '
        '(e.g. Bambu Lab with AMS). The Ender does not support it.',
    'Costo por hora ($/h)': 'Cost per hour ($/h)',
    'Costo horario de la máquina (amortización, energía, mantenimiento). '
    'Se usa para calcular la depreciación acumulada: horas impresas × este costo.':
        'Hourly cost of the machine (amortization, power, maintenance). '
        'Used to compute accumulated depreciation: hours printed × this cost.',
    'Horas impresas (acumuladas)': 'Hours printed (accumulated)',
    'Horas de impresión de los trabajos ya terminados en esta máquina. '
    'Se recalcula automáticamente al marcar un trabajo como Impreso.':
        'Print hours of the jobs already finished on this machine. '
        'Recalculated automatically when a job is marked as Printed.',
    'Máquina (impresora)': 'Machine (printer)',
    'Máquinas (impresoras)': 'Machines (printers)',
    'En cola': 'Queued',
    'Imprimiendo': 'Printing',
    'Impreso': 'Printed',
    'Pieza concreta que imprime este trabajo. Si está vacío es un '
    'trabajo a nivel producto (modo anterior).':
        'Specific part this job prints. If empty, it is a product-level '
        'job (legacy mode).',
    'Cantidad de piezas a imprimir': 'Quantity of parts to print',
    'Unidades de la pieza que hay que imprimir para este pedido.':
        'Units of the part that must be printed for this order.',
    'Máquina asignada. El sistema recomienda una, podés cambiarla.':
        'Assigned machine. The system recommends one, you can change it.',
    'Orden en la cola': 'Order in the queue',
    'Posición dentro de la cola de la máquina (menor = primero).':
        "Position within the machine's queue (lower = first).",
    'Inicio estimado': 'Estimated start',
    'Fin de impresión estimado': 'Estimated print end',
    'Inicio real': 'Actual start',
    'Fin real': 'Actual end',
    'Material descontado': 'Material deducted',
    'Se marca cuando se descontó el filamento de este trabajo (al aprobar el pedido).':
        "Set when this job's filament has been deducted (when the order is approved).",
    'Sobrante sumado a stock': 'Surplus added to stock',
    'Se marca cuando, al imprimirse, la sobrante del último gcode se '
    'sumó al stock de la pieza.':
        'Set when, upon printing, the surplus of the last gcode was '
        "added to the part's stock.",
    'Guardado en el historial': 'Saved to history',
    'Se marca cuando, al imprimirse, el trabajo se guardó en el '
    'historial de la máquina. Evita duplicar el registro.':
        'Set when, upon printing, the job was saved to the '
        "machine's history. Prevents duplicate records.",
    '%(nombre)s ×%(qty)s — Pedido #%(pid)s (%(cliente)s)':
        '%(nombre)s ×%(qty)s — Order #%(pid)s (%(cliente)s)',
    'Título': 'Title',
    'Horas de impresión': 'Print hours',
    'Finalizado el': 'Finished on',
    'Impresión del historial': 'History print',
    'Historial de impresiones': 'Print history',
    'Trabajo de producción': 'Production job',
    'Trabajos de producción': 'Production jobs',
    'Tablero de producción': 'Production dashboard',
    "'%(nombre)s' es multicolor y '%(machine)s' no "
    "imprime multicolor. Asigná una máquina con AMS.":
        "'%(nombre)s' is multicolor and '%(machine)s' does not "
        "print multicolor. Assign a machine with AMS.",
    'Solo se puede marcar obsoleta una impresión por pieza.':
        'Only a per-part print can be marked obsolete.',
    'Solo se puede marcar obsoleta una impresión En cola o '
    'Imprimiendo (todavía no terminada).':
        'Only a Queued or Printing print (not yet finished) can be '
        'marked obsolete.',
    'Esta impresión no tiene material descontado, no hay nada que '
    'reponer.':
        'This print has no material deducted, there is nothing to '
        'restock.',
    'Impresión %(pieza)s (%(producto)s) ×%(quantity)s':
        'Print %(pieza)s (%(producto)s) ×%(quantity)s',
    ' (faltaron %(shortage)s g: quedó en negativo)':
        ' (%(shortage)s g short: went negative)',
    'Impresión %(producto)s ×%(quantity)s':
        'Print %(producto)s ×%(quantity)s',
    'Impresión obsoleta %(pieza)s (%(producto)s): '
    'se perdieron %(scrap)s g, vuelven %(devuelto)s g al stock. '
    'Se reimprime.':
        'Obsolete print %(pieza)s (%(producto)s): '
        '%(scrap)s g were lost, %(devuelto)s g return to stock. '
        'It will be reprinted.',
    'Trabajos en cola': 'Jobs in queue',
    'Depreciación acumulada': 'Accumulated depreciation',
    "'%(name)s' quedó inactiva: %(count)s trabajo(s) en cola se "
    "liberaron (sin máquina). Reasignalos a otra impresora.":
        "'%(name)s' became inactive: %(count)s job(s) in queue were "
        "released (no machine). Reassign them to another printer.",
    "'%(name)s' dejó de imprimir multicolor: %(count)s trabajo(s) "
    "multicolor en cola se liberaron (sin máquina). Reasignalos a "
    "una impresora con AMS.":
        "'%(name)s' stopped printing multicolor: %(count)s multicolor "
        "job(s) in queue were released (no machine). Reassign them to "
        "a printer with AMS.",
    'Marcar impresión obsoleta (reimprimir)': 'Mark print obsolete (reprint)',
    '%(count)s trabajo(s) no se pueden marcar obsoletos '
    '(deben ser por pieza, En cola/Imprimiendo y con material '
    'descontado). Se ignoraron.':
        '%(count)s job(s) cannot be marked obsolete '
        '(they must be per-part, Queued/Printing and with material '
        'deducted). They were ignored.',
    '%(grams)s g de %(item)s': '%(grams)s g of %(item)s',
    "Impresión '%(job)s' marcada obsoleta y devuelta a la cola: "
    "se perdieron %(scrap)s g; volvieron al stock "
    "%(devuelto)s. Se reimprime y vuelve a descontar el "
    "material al terminar.":
        "Print '%(job)s' marked obsolete and returned to the queue: "
        "%(scrap)s g were lost; %(devuelto)s returned to stock. "
        "It will be reprinted and will deduct the material again when finished.",
    '0 g': '0 g',
    'Marcar impresiones obsoletas': 'Mark prints obsolete',
    'Inicio est.': 'Est. start',
    'Fin impr. est.': 'Est. print end',
    "Trabajo '%(obj)s' impreso: %(surplus)s unidad(es) sobrante(s) "
    "se sumaron al stock de la pieza.":
        "Job '%(obj)s' printed: %(surplus)s surplus unit(s) "
        "were added to the part's stock.",
    "El presupuesto #%(id)s pasó a "
    "'%(status)s' según su cola de "
    "producción.":
        "Budget #%(id)s moved to "
        "'%(status)s' according to its production "
        "queue.",
    'Cola de producción': 'Production queue',
    'Libre ahora': 'Free now',
    'Ventana de carga: <b>%(window)s</b>. Las máquinas imprimen de corrido (un trabajo\n'
    '    puede cruzar la noche), pero un trabajo nuevo solo arranca dentro de esa franja.\n'
    '    El post-proceso se suma después, sobre la fecha de entrega de cada presupuesto.':
        'Loading window: <b>%(window)s</b>. Machines print continuously (a job\n'
        '    may cross the night), but a new job only starts within that window.\n'
        "    Post-processing is added afterward, on each quote's delivery date.",
    'Cola por máquina': 'Queue per machine',
    'Libre:': 'Free:',
    'Inicio': 'Start',
    'Fin impr.': 'Print end',
    'imprimiendo': 'printing',
    'Sin trabajos en cola.': 'No jobs in queue.',
    'No hay máquinas activas. Cargá máquinas en "Máquinas (impresoras)".':
        'There are no active machines. Add machines under "Machines (printers)".',
    '%(n)s trabajo(s) sin máquina asignada.':
        '%(n)s job(s) with no machine assigned.',
    'Asignales una máquina en "Trabajos de producción" para que entren a la cola.':
        'Assign them a machine under "Production jobs" so they enter the queue.',
    'Cola total': 'Full queue',
    'Máquina / estado': 'Machine / status',
    'No hay trabajos en cola.': 'There are no jobs in the queue.',
    'Vista general de producción. Ventana de carga <b>%(window)s</b>. Los tiempos son\n'
    '    estimados según la cola actual; el post-proceso se suma sobre la entrega de cada presupuesto.':
        'Production overview. Loading window <b>%(window)s</b>. Times are\n'
        "    estimated from the current queue; post-processing is added on each quote's delivery.",
    'imprimiendo ahora': 'printing now',
    'en cola (sin arrancar)': 'queued (not started)',
    'horas de impresión pendientes': 'pending print hours',
    'pedidos en producción': 'orders in production',
    'Qué se está imprimiendo': 'What is printing',
    'próximo': 'next',
    'Termina:': 'Finishes:',
    '%(n)s en cola en esta máquina': '%(n)s queued on this machine',
    'Libre — sin trabajos en cola.': 'Free — no jobs in queue.',
    'No hay máquinas activas.': 'There are no active machines.',
    'Máquinas fuera de servicio': 'Machines out of service',
    'inactiva': 'inactive',
    'Sin nota. Anotá qué está roto en la máquina (campo "Notas").':
        'No note. Write down what is broken on the machine ("Notes" field).',
    'Trabajos sin máquina asignada': 'Jobs with no machine assigned',
    'Estos trabajos no tienen impresora asignada (sin máquina o máquina inactiva). Asignales una desde "Trabajos de producción" para que entren en la cola.':
        'These jobs have no printer assigned (no machine or inactive machine). Assign one from "Production jobs" so they enter the queue.',
    'Próximas entregas': 'Upcoming deliveries',
    'Entrega estimada': 'Estimated delivery',
    '(fijada a mano)': '(set manually)',
    'No hay pedidos en producción con fecha de entrega.':
        'There are no orders in production with a delivery date.',
    'Comprar materia prima': 'Buy raw material',
    'Después de aprobar los pedidos, estos insumos quedaron por debajo del mínimo (un stock negativo significa que ya te comprometiste con más de lo que tenías). Comprá al menos lo indicado antes de la fecha en que la máquina lo necesita.':
        'After approving the orders, these supplies fell below the minimum (a negative stock means you already committed to more than you had). Buy at least the indicated amount before the date the machine needs it.',
    'Stock actual (g)': 'Current stock (g)',
    'Mínimo (g)': 'Minimum (g)',
    'Comprar (g)': 'Buy (g)',
    'Se necesita aprox.': 'Needed approx.',
    'Stock actual': 'Current stock',
    'Mínimo': 'Minimum',
    'Comprar': 'Buy',
    'Todos los insumos están por encima de su stock mínimo. No hace falta comprar.':
        'All supplies are above their minimum stock. No need to buy.',
    'Estas impresiones salieron mal y se van a <b>reimprimir</b>. Cargá cuántos\n'
    '    <b>gramos de filamento se perdieron</b> en cada impresión fallida. La diferencia\n'
    '    entre el total de la pieza y lo perdido <b>vuelve al stock de filamento</b>; la\n'
    '    pieza vuelve a la cola y al reimprimirse vuelve a descontar el material completo.\n'
    '    Los agregados no se tocan (se usan recién al armar la pieza ya impresa).':
        'These prints came out wrong and will be <b>reprinted</b>. Enter how many\n'
        '    <b>grams of filament were lost</b> on each failed print. The difference\n'
        "    between the part's total and what was lost <b>returns to filament stock</b>; the\n"
        '    part goes back to the queue and, when reprinted, deducts the full material again.\n'
        '    Aggregates are not touched (they are used only when assembling the already-printed part).',
    'Impresión': 'Print',
    'Pieza / Producto': 'Part / Product',
    'Total filamento (g)': 'Total filament (g)',
    'Gramos perdidos (obsoleto)': 'Grams lost (obsolete)',
    'Marcar obsoletas y reimprimir': 'Mark obsolete and reprint',
    'Cancelar': 'Cancel',
    'No se puede perder más que el total de filamento de la pieza.':
        "You cannot lose more than the part's total filament.",
}

# Cadenas de {% blocktranslate %}: el msgid real que arma Django usa
# %(var)s para las variables y DUPLICA el % literal (%%). Estas claves se
# verificaron extrayendo el msgid exacto con django ...template.templatize.
BLOCKTRANS = {
    # gastos/panel.html
    '%(n_gastos)s gasto(s)': '%(n_gastos)s expense(s)',
    'vs %(prev_label)s': 'vs %(prev_label)s',
    'antes: %(prev_total)s': 'before: %(prev_total)s',
    '%(meses_transcurridos)s mes(es)': '%(meses_transcurridos)s month(s)',
    'Evolución mensual (%(sel_year)s)': 'Monthly trend (%(sel_year)s)',
    '%% usado': '%% used',
    # budgets/metricas.html
    'Facturación por %(pl)s': 'Billing per %(pl)s',
    '%(reprints)s por falla': '%(reprints)s due to failure',
    '%(total_ent)s con fecha': '%(total_ent)s with a date',
    '%(n_compras)s compra/s': '%(n_compras)s purchase/s',
}

# OVERRIDES: resuelven choques de la misma cadena entre apps (un msgid =
# una sola traducción). "Presupuesto" debe ser SIEMPRE "Budget" (no "Quote").
OVERRIDES = {
    'Presupuesto': 'Budget',
}

_EN = {}
for _d in (COMMON, INVENTORY, GASTOS, BUDGETS, PRODUCTION, BLOCKTRANS, OVERRIDES):
    _EN.update(_d)

TRANSLATIONS = {'en': _EN}


def write_po(path: Path, catalog: dict[str, str], lang: str) -> None:
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        f'"Language: {lang}\\n"',
        '',
    ]
    for src, dst in catalog.items():
        src_esc = src.replace('\\', '\\\\').replace('"', '\\"')
        dst_esc = dst.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'msgid "{src_esc}"')
        lines.append(f'msgstr "{dst_esc}"')
        lines.append('')
    path.write_text('\n'.join(lines), encoding='utf-8')


def write_mo(path: Path, catalog: dict[str, str]) -> None:
    """Genera un .mo binário (formato GNU MO) desde un dict.

    Basado en el algoritmo de Tools/i18n/msgfmt.py de CPython.
    """
    # La entrada vacía ("") con las cabeceras es obligatoria.
    items = {'': 'Content-Type: text/plain; charset=UTF-8\n'}
    items.update(catalog)

    keys = sorted(items.keys())
    offsets = []
    ids = b''
    strs = b''
    for key in keys:
        msgid = key.encode('utf-8')
        msgstr = items[key].encode('utf-8')
        offsets.append((len(ids), len(msgid), len(strs), len(msgstr)))
        ids += msgid + b'\x00'
        strs += msgstr + b'\x00'

    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    offsets_arr = array.array('i', koffsets + voffsets)

    output = struct.pack(
        'Iiiiiii',
        0x950412DE,        # magic
        0,                 # version
        len(keys),         # number of entries
        7 * 4,             # start of key index
        7 * 4 + len(keys) * 8,  # start of value index
        0, 0,              # size/offset of hash table
    )
    output += offsets_arr.tobytes()
    output += ids
    output += strs
    path.write_bytes(output)


def main() -> None:
    for lang, catalog in TRANSLATIONS.items():
        out_dir = BASE_DIR / 'locale' / lang / 'LC_MESSAGES'
        out_dir.mkdir(parents=True, exist_ok=True)
        write_po(out_dir / 'django.po', catalog, lang)
        write_mo(out_dir / 'django.mo', catalog)
        print(f'  {lang}: {len(catalog)} cadenas -> {out_dir}/django.(po|mo)')
    print('OK')


if __name__ == '__main__':
    main()
