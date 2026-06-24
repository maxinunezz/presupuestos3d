"""
Genera un PDF instructivo del panel de administración de presupuestos3d.

Uso:
    source venv/bin/activate
    python scripts/generar_instructivo.py

El PDF se guarda en /home/pmaximiliano/Escritorio/3darg/.
"""

import base64
from io import BytesIO
from pathlib import Path

from xhtml2pdf import pisa

BASE_DIR = Path(__file__).resolve().parent.parent
LOGO_PATH = BASE_DIR / "budgets" / "assets" / "logo3darg.jpeg"
OUTPUT_PATH = Path("/home/pmaximiliano/Escritorio/3darg/instructivo_admin_3darg.pdf")


def logo_data_uri() -> str:
    try:
        data = LOGO_PATH.read_bytes()
    except FileNotFoundError:
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8" />
<style>
    @page {{
        size: a4 portrait;
        margin: 2.2cm 1.8cm 2.2cm 1.8cm;
        @frame footer {{
            -pdf-frame-content: footerContent;
            bottom: 1cm; left: 1.8cm; right: 1.8cm; height: 1cm;
        }}
    }}
    @page cover {{
        size: a4 portrait;
        margin: 0;
    }}
    body {{ font-family: Helvetica, Arial, sans-serif; color: #1a1a1a; font-size: 10.5pt; line-height: 1.5; }}
    h1 {{ font-size: 26pt; color: #000; margin: 0; letter-spacing: -0.5px; }}
    h2 {{
        font-size: 13.5pt; color: #fff; background: #000;
        padding: 6px 10px; margin: 26px 0 10px 0;
    }}
    h3 {{
        font-size: 11.5pt; color: #000; margin: 14px 0 4px 0;
        border-left: 3px solid #000; padding-left: 8px;
    }}
    p {{ margin: 5px 0; }}
    ul {{ margin: 5px 0 9px 0; }}
    li {{ margin: 3px 0; }}

    /* PORTADA (contenido inline para que el fondo negro quede continuo en pisa) */
    .cover-card {{ background: #000; padding: 56px 40px; text-align: center; margin-top: 26px; line-height: 1.25; }}
    .cover-card img {{ width: 118px; }}
    .ct {{ color: #fff; font-size: 27pt; letter-spacing: -0.5px; }}
    .cs {{ color: #bdbdbd; font-size: 11.5pt; }}
    .spacer-lg {{ font-size: 20pt; line-height: 1; }}
    .spacer-sm {{ font-size: 9pt; line-height: 1; }}
    .cover-lead {{ color: #333; font-size: 11.5pt; text-align: center; line-height: 1.7; padding-top: 36px; }}
    .cover-rule {{ border-top: 2px solid #000; width: 56px; margin: 30px auto 0 auto; }}
    .cover-meta {{ color: #888; font-size: 9.5pt; text-align: center; padding-top: 36px; }}

    .lead {{ color: #333; font-size: 11pt; }}

    /* CAJAS monocromas */
    .box {{ background: #f2f2f2; border-left: 4px solid #000; padding: 9px 12px; margin: 9px 0; }}
    .box b {{ color: #000; }}
    .warn {{ background: #fff; border: 1.5px solid #000; padding: 9px 12px; margin: 9px 0; }}
    .warn b {{ color: #000; }}
    .ok {{ background: #1a1a1a; color: #fff; padding: 9px 12px; margin: 9px 0; }}
    .ok b {{ color: #fff; }}
    .ok ul {{ margin: 5px 0 2px 0; }}

    table {{ width: 100%; border-collapse: collapse; margin: 9px 0; font-size: 9.5pt; }}
    th {{ background: #000; color: #fff; text-align: left; padding: 6px 8px; }}
    td {{ border-bottom: 1px solid #ccc; padding: 6px 8px; vertical-align: top; background: #fff; }}

    .step {{ font-weight: bold; color: #000; }}
    .footer {{ color: #999; font-size: 8.5pt; text-align: center; border-top: 1px solid #e5e5e5; padding-top: 5px; }}
    .pagebreak {{ page-break-before: always; }}

    /* INDICE */
    .toc {{ list-style: none; margin: 6px 0 0 0; padding: 0; }}
    .toc li {{ margin: 0; padding: 7px 4px; border-bottom: 1px solid #e5e5e5; font-size: 10.5pt; }}
    .toc .tn {{ display: inline-block; width: 26px; color: #000; font-weight: bold; }}
</style>
</head>
<body>

<div id="footerContent" class="footer">
    3darg &nbsp;·&nbsp; Manual del panel de administración &nbsp;·&nbsp; pág. <pdf:pagenumber>
</div>

<!-- PORTADA -->
<div class="cover-card">
    {logo}<br/>
    <span class="spacer-lg"><br/></span>
    <span class="ct">Manual del panel<br/>de administración</span><br/>
    <span class="spacer-sm"><br/></span>
    <span class="cs">Costeo · Presupuestos · Inventario · Producción</span>
</div>
<div class="cover-rule"></div>
<p class="cover-lead">
    Guía completa de uso del sistema de gestión de 3darg.<br/>
    Pensada para que cualquier persona, sin haberlo visto antes,<br/>
    pueda manejarlo de punta a punta.
</p>
<div class="cover-meta">
    3darg · Impresión 3D · 3darg1@gmail.com<br/>
    Documento de uso interno
</div>

<div class="pagebreak"></div>

<!-- INDICE -->
<h2>Contenido</h2>
<ul class="toc">
    <li><span class="tn">1</span> Qué es este sistema y cómo entrar</li>
    <li><span class="tn">2</span> Cómo cambia el stock (regla de oro)</li>
    <li><span class="tn">3</span> La campanita de bajo stock</li>
    <li><span class="tn">4</span> Filamentos</li>
    <li><span class="tn">5</span> Agregados</li>
    <li><span class="tn">6</span> Totales de inventario</li>
    <li><span class="tn">7</span> Compras</li>
    <li><span class="tn">8</span> Movimientos de stock</li>
    <li><span class="tn">9</span> Ajustes manuales de stock</li>
    <li><span class="tn">10</span> Costeo de productos</li>
    <li><span class="tn">11</span> Presupuestos</li>
    <li><span class="tn">12</span> Máquinas (impresoras)</li>
    <li><span class="tn">13</span> Trabajos de producción</li>
    <li><span class="tn">14</span> Cola de producción</li>
    <li><span class="tn">15</span> Tablero de producción</li>
    <li><span class="tn">16</span> ¿Cuándo se descuenta el material?</li>
    <li><span class="tn">17</span> Flujo de trabajo recomendado</li>
</ul>

<div class="pagebreak"></div>

<!-- INTRO -->
<h2>1. Qué es este sistema y cómo entrar</h2>
<p class="lead">
    Este panel maneja todo el circuito de tu negocio de impresión 3D en un solo lugar: cargás
    tus materiales y su stock, registrás compras, costeás cada pieza, armás presupuestos para
    el cliente y, cuando los aprobás, el sistema arma solo la <b>cola de producción</b> de tus
    impresoras y te dice <b>cuándo entregás</b> y <b>qué materia prima vas a tener que comprar</b>.
</p>
<p>Para entrar abrís el navegador en la dirección del panel y, al estar adentro, vas a ver en la
   página de inicio una lista de secciones agrupadas en tres bloques:</p>
<ul>
    <li><b>Inventario:</b> Filamentos, Agregados, Totales de inventario, Compras, Movimientos de
        stock y Ajustes manuales de stock.</li>
    <li><b>Costeo y presupuestos:</b> Costeo de productos y Presupuestos.</li>
    <li><b>Producción:</b> Máquinas, Trabajos de producción, Cola de producción y Tablero de
        producción.</li>
</ul>
<div class="box">
    <b>Cómo se navega:</b> hacés clic en una sección para ver su lista. Arriba a la derecha de
    cada lista hay un botón "Agregar" para crear un registro nuevo. Hacés clic en cualquier fila
    para abrirla y editarla. Casi todas las listas tienen un <b>buscador</b> y <b>filtros</b> a
    la derecha para encontrar lo que necesitás.
</div>

<!-- REGLA DE ORO -->
<h2>2. Cómo cambia el stock (regla de oro)</h2>
<p>El stock de cada material <b>no se escribe a mano</b> en la ficha del material. Cambia solo
   por vías controladas, para que siempre quede registrado <i>por qué</i> cambió cada cantidad:</p>
<table>
    <tr><th>Vía</th><th>Efecto</th></tr>
    <tr><td><b>Compra confirmada</b></td><td>Suma stock (y actualiza el costo si cambió el precio).</td></tr>
    <tr><td><b>Ajuste manual</b></td><td>Suma o resta para corregir diferencias con la realidad.</td></tr>
    <tr><td><b>Impresión terminada</b></td><td>Resta el material gastado cuando un trabajo se marca como "Impreso".</td></tr>
</table>
<div class="box">
    <b>Importante:</b> aprobar un presupuesto <b>no</b> descuenta material. El descuento ocurre
    más adelante, cuando cada pieza se imprime. El porqué de esto está explicado en el punto 16.
</div>

<!-- CAMPANITA -->
<h2>3. La campanita de bajo stock</h2>
<p>Arriba a la derecha del panel, al lado de tu usuario, hay una <b>campanita</b> 🔔. Si tenés
   materiales por debajo de su mínimo, la campanita muestra un número rojo con cuántos son.</p>
<h3>Cómo funciona</h3>
<ul>
    <li>Cada filamento y cada agregado tiene un campo <b>"Stock mínimo"</b>. Vos definís ese
        mínimo por material, porque no es lo mismo el filamento negro que el cian, ni las
        argollas que las pelotas.</li>
    <li>Cuando el stock de un material cae por debajo de su mínimo, ese material entra en la
        lista de la campanita.</li>
    <li>Si pasás el mouse por la campanita, se despliega la lista de qué está bajo. Si hacés
        clic, te lleva directo a la lista filtrada por "bajo stock".</li>
</ul>
<div class="box">
    <b>Tip:</b> el mínimo de los agregados se mide en la <b>misma unidad</b> que el agregado
    (unidades para pelotas, gramos para argollas que van por peso). Poné el mínimo en el número
    que te dé tranquilidad para no quedarte sin material en medio de un pedido.
</div>

<div class="pagebreak"></div>

<!-- FILAMENTOS -->
<h2>4. Filamentos</h2>
<p>Es el catálogo de tus rollos de filamento. Cada filamento guarda marca, tipo, color, costo
   por kilo, stock actual en gramos y su stock mínimo.</p>
<h3>Cómo se usa</h3>
<ul>
    <li>Para <b>crear</b> uno: botón "Agregar filamento". Completá marca/tipo/color y el costo por kilo.</li>
    <li>Desde la lista podés editar directo el <b>costo por kilo</b>, el <b>stock mínimo</b> y si está activo.</li>
    <li>El <b>stock en gramos</b> aparece pero está bloqueado: no se toca a mano (ver regla de oro).</li>
    <li>La columna de estado te muestra con un cartel si el filamento está <b>bajo</b> su mínimo o en <b>OK</b>.</li>
</ul>
<div class="warn">
    <b>Importante:</b> al crear un filamento nuevo NO cargues stock inicial a mano. Si ya tenés
    rollos, cargá ese stock con un <b>Ajuste manual</b> o registralo como <b>Compra</b>.
</div>

<!-- AGREGADOS -->
<h2>5. Agregados</h2>
<p>Son los insumos que no son filamento: tornillos, imanes, pinturas, cajas, argollas, pelotas, etc.
   Cada agregado tiene nombre, costo unitario, stock y stock mínimo.</p>
<h3>Cómo se usa</h3>
<ul>
    <li>Funciona igual que Filamentos: se crea desde "Agregar agregado".</li>
    <li>Algunos agregados se miden <b>por unidad</b> (pelotas) y otros <b>por peso</b> (argollas).
        El sistema lo tiene en cuenta tanto para el stock como para el mínimo y la campanita.</li>
    <li>El <b>costo</b>, el <b>stock mínimo</b> y el estado se editan desde la lista; el <b>stock</b>
        está bloqueado.</li>
</ul>

<!-- TOTALES -->
<h2>6. Totales de inventario</h2>
<p>Vista de solo lectura para ver de un vistazo cuánto valor tenés inmovilizado en stock. Suma
   filamentos y agregados valorizados según su costo actual. Sirve para saber cuánta plata
   tenés "guardada" en materiales sin sumar a mano.</p>

<div class="pagebreak"></div>

<!-- COMPRAS -->
<h2>7. Compras</h2>
<p>Acá registrás lo que le comprás a tus proveedores. Una compra suma stock y, si corresponde,
   actualiza el costo del material. Trabaja en dos estados:</p>
<table>
    <tr><th>Estado</th><th>Qué significa</th></tr>
    <tr><td><b>Borrador</b></td><td>La estás armando. Todavía NO toca el inventario. Podés editar libremente las líneas.</td></tr>
    <tr><td><b>Confirmada</b></td><td>Recién acá se suma el stock, se actualiza el precio y se registran los movimientos. Ya no se edita.</td></tr>
</table>
<h3>Cómo cargar una compra</h3>
<ul>
    <li><span class="step">1.</span> Botón "Agregar compra". Cargá proveedor, número de factura (opcional) y notas.</li>
    <li><span class="step">2.</span> En las líneas, elegí un filamento o agregado <b>ya existente</b> con el buscador. Esto evita duplicados.</li>
    <li><span class="step">3.</span> Cargá la cantidad (gramos para filamento, unidades para agregado) y, si cambió, el precio. Para filamento el precio es <b>por kilo</b>.</li>
    <li><span class="step">4.</span> Si el precio cambió respecto del actual, al confirmar se actualiza el costo del material.</li>
    <li><span class="step">5.</span> Cuando está todo, cambiá el estado a <b>Confirmada</b> y guardá. Ahí se aplica al inventario.</li>
</ul>
<div class="warn">
    <b>Mientras esté en Borrador, el stock no se mueve.</b> El material no aparece sumado hasta que confirmes la compra.
</div>

<!-- MOVIMIENTOS -->
<h2>8. Movimientos de stock</h2>
<p>Es el historial completo de cada entrada y salida de material. Cada movimiento dice qué
   material, cuánto, cuándo y por qué motivo:</p>
<table>
    <tr><th>Motivo</th><th>Origen</th></tr>
    <tr><td>Compra</td><td>Una compra confirmada (suma).</td></tr>
    <tr><td>Producción (impresión)</td><td>Una pieza se marcó como "Impreso" y se descontó su material (resta).</td></tr>
    <tr><td>Ajuste manual</td><td>Corregiste el stock a mano (suma o resta).</td></tr>
</table>
<p>Esta vista es de lectura: no se edita acá, sirve para auditar y entender por qué cambió el stock.</p>

<!-- AJUSTES -->
<h2>9. Ajustes manuales de stock</h2>
<p>Sirve para corregir el stock cuando hay diferencias con la realidad (se rompió un rollo,
   contaste y había de más o de menos, carga inicial de inventario, etc.).</p>
<h3>Cómo se usa</h3>
<ul>
    <li>Elegí el filamento <b>o</b> el agregado a ajustar (uno solo por ajuste).</li>
    <li>En cantidad poné el número: <b>positivo suma</b>, <b>negativo resta</b>. Filamento en
        gramos, agregado en su unidad. Ej: <i>500</i> agrega 500; <i>-200</i> quita 200.</li>
    <li>Escribí siempre el motivo en la nota.</li>
</ul>
<div class="box">
    <b>Tip:</b> si querés dejar el stock en un valor exacto, fijate cuánto hay hoy y cargá la
    diferencia. El stock nunca queda en negativo: si restás de más, queda en 0.
</div>

<div class="pagebreak"></div>

<!-- COSTEO DE PRODUCTOS -->
<h2>10. Costeo de productos</h2>
<p>Acá definís cuánto te cuesta y a cuánto vendés <b>una pieza</b>. Cada producto reúne todos
   sus costos y calcula solo el precio de venta final.</p>
<h3>Qué cargás en cada producto</h3>
<ul>
    <li><b>Datos:</b> nombre, descripción, si es <b>multicolor</b> y si está activo (solo los activos se usan en presupuestos).</li>
    <li><b>Material:</b> líneas de filamento (gramos por pieza) y de agregados (cantidad por pieza). El sistema toma el costo de cada material.</li>
    <li><b>Impresión y máquina:</b> horas de impresión por pieza, costo de máquina por hora y % de merma (material que se desperdicia).</li>
    <li><b>Mano de obra / post-proceso:</b> horas de post-proceso por pieza y costo por hora. El post-proceso es el armado, lijado, pintado, pegado de agregados, etc.</li>
    <li><b>Precio:</b> margen de ganancia (%) y redondeo.</li>
    <li><b>Archivo del modelo:</b> opcional, podés guardar el gcode y subir el archivo .3mf/.stl.</li>
</ul>
<div class="box">
    <b>Multicolor (AMS):</b> marcá esta casilla si la pieza usa varios colores en simultáneo. El
    sistema solo va a poder mandarla a una impresora con AMS (las Bambu Lab), nunca a la Ender.
    Más detalle en el punto 12.
</div>
<h3>Resumen de costos</h3>
<p>Al guardar, el producto muestra un desglose: material (+ merma), agregados, máquina, mano de
   obra, costo total por pieza, margen aplicado y <b>precio de venta final</b>. Ese desglose es
   interno: el cliente nunca lo ve.</p>

<!-- PRESUPUESTOS -->
<h2>11. Presupuestos</h2>
<p>Un presupuesto es la cotización para un cliente. Agrupa varios productos ya costeados, cada
   uno con su cantidad, y genera el PDF para enviar.</p>
<h3>Cómo armar un presupuesto</h3>
<ul>
    <li><span class="step">1.</span> "Agregar presupuesto": cargá el nombre del cliente y una descripción.</li>
    <li><span class="step">2.</span> Agregá los productos (solo activos) con su cantidad. El precio sale del producto, pero podés sobreescribirlo.</li>
    <li><span class="step">3.</span> Si hay un <b>costo fijo</b> (envío, diseño, etc.) cargalo aparte. Ajustá el redondeo del total.</li>
    <li><span class="step">4.</span> Guardá: vas a ver el resumen con subtotal, costo fijo, total y cantidad de piezas.</li>
</ul>
<h3>Estados del presupuesto</h3>
<p>Borrador → Enviado → Aprobado → En producción → Completado (o Cancelado). El estado te ayuda
   a seguir en qué etapa está cada pedido. Cada cambio de estado importante queda registrado
   con su fecha (enviado, aprobado, inicio y fin de producción, completado).</p>
<h3>Aprobar (arma la cola de producción)</h3>
<div class="ok">
    <b>Al aprobar un presupuesto, el sistema:</b><br/>
    &nbsp;&nbsp;–&nbsp; Crea un <b>trabajo de producción</b> por cada producto del pedido.<br/>
    &nbsp;&nbsp;–&nbsp; Le <b>recomienda una impresora</b> a cada trabajo, balanceando las colas y respetando si la pieza es multicolor.<br/>
    &nbsp;&nbsp;–&nbsp; Calcula la <b>fecha de entrega estimada</b> (impresión de la cola + post-proceso total).<br/>
    &nbsp;&nbsp;–&nbsp; Te <b>avisa</b> si el stock no va a alcanzar, pero NO descuenta material todavía.<br/>
    <br/>
    Usá la acción "Aprobar presupuesto(s) y generar cola de producción" desde la lista.
</div>
<h3>Producción y entrega</h3>
<p>Dentro del presupuesto vas a ver un resumen de a qué máquina fue cada producto, las horas de
   impresión, las horas de post-proceso totales y la <b>entrega estimada</b>. La fecha de entrega
   se calcula sola, pero si la <b>editás a mano</b> queda fija y el sistema no te la pisa al
   recalcular la cola.</p>
<h3>PDF para el cliente</h3>
<p>Cada presupuesto tiene un botón <b>"PDF cliente"</b>. Ese PDF muestra solo lo que el cliente
   debe ver: producto, cantidad, precio unitario y total. <b>Nunca</b> muestra el desglose
   interno de costos ni la cola de producción.</p>

<div class="pagebreak"></div>

<!-- MAQUINAS -->
<h2>12. Máquinas (impresoras)</h2>
<p>Es el listado de tus impresoras. Cada máquina activa procesa su propia cola de trabajos, así
   que varias piezas se imprimen en paralelo. Hoy tenés tres: dos Bambu Lab A1 Combo y una
   Ender 3 V3 Plus.</p>
<h3>Qué guarda cada máquina</h3>
<ul>
    <li><b>Activa:</b> si está inactiva, no se le asignan trabajos nuevos ni cuenta para la cola.</li>
    <li><b>Imprime multicolor (AMS):</b> marca si la máquina puede imprimir varios colores en
        simultáneo. Las Bambu Lab tienen AMS (sí); la Ender no.</li>
    <li><b>Trabajos en cola:</b> cuántos trabajos pendientes/imprimiendo tiene.</li>
</ul>
<div class="box">
    <b>Cómo se respeta el multicolor:</b> si un producto está marcado como multicolor, el sistema
    solo lo manda a una máquina con AMS. Aunque las dos Bambu estén ocupadas, nunca lo manda a la
    Ender: lo deja "sin máquina" para que vos decidas. Y si intentás asignarlo a mano a la Ender,
    el panel lo rechaza con un aviso. Las piezas de un solo color pueden ir a cualquier máquina.
</div>
<div class="warn">
    <b>Si desactivás una máquina</b> (por ejemplo, porque se rompió), sus trabajos pendientes no
    se pierden: el sistema los <b>libera</b> automáticamente (quedan "sin máquina") y te avisa para
    que los reasignes a otra impresora. Vas a verlos listados en el Tablero y en la Cola, en la
    sección "Trabajos sin máquina asignada".
</div>

<!-- TRABAJOS -->
<h2>13. Trabajos de producción</h2>
<p>Un trabajo es <b>un producto de un presupuesto</b> (con su cantidad) asignado a una máquina y
   con una posición en la cola de esa máquina. Un mismo presupuesto puede tener varios trabajos
   repartidos en distintas impresoras.</p>
<h3>Qué podés hacer</h3>
<ul>
    <li>Desde la lista podés cambiar directo la <b>máquina</b>, el <b>orden</b> en la cola y el <b>estado</b>.</li>
    <li>Estados: <b>En cola</b> → <b>Imprimiendo</b> → <b>Impreso</b> (o <b>Cancelado</b>).</li>
    <li>Al marcar <b>Imprimiendo</b>, se registra el inicio real.</li>
    <li>Al marcar <b>Impreso</b>, se <b>descuenta el material</b> de ese trabajo del inventario (una sola vez).</li>
    <li>Cualquier cambio de máquina, orden o estado <b>recalcula la cola</b> y las fechas estimadas.</li>
</ul>
<div class="box">
    <b>Ventana de carga:</b> las impresoras imprimen de corrido (un trabajo puede cruzar la noche),
    pero un trabajo nuevo solo <b>arranca</b> entre las 07:00 y las 23:00, porque alguien tiene que
    cargar la pieza. Si una máquina queda libre de madrugada, el siguiente arranca a las 07:00.
</div>

<!-- COLA -->
<h2>14. Cola de producción</h2>
<p>Vista de solo lectura con la cola de cada máquina: qué trabajos tiene, en qué orden, con su
   inicio y fin de impresión estimados, y cuándo queda libre cada impresora. Abajo muestra la
   <b>cola total</b> de todos los trabajos ordenados por inicio. Sirve para ver el panorama de
   carga de todas las máquinas de un vistazo.</p>

<!-- TABLERO -->
<h2>15. Tablero de producción</h2>
<p>Es la pantalla "entro y me dice todo". De solo lectura, reúne en un solo lugar:</p>
<ul>
    <li><b>Qué se está imprimiendo</b> ahora en cada máquina y cuándo termina.</li>
    <li><b>Próximas entregas:</b> los presupuestos aprobados o en producción ordenados por fecha de entrega.</li>
    <li><b>Comprar materia prima:</b> cruza la cola de producción contra el stock real y te avisa
        qué filamento o agregado no va a alcanzar, cuánto falta comprar y <b>cuándo</b> te vas a
        quedar sin ese material.</li>
    <li>Indicadores rápidos: trabajos en cola, horas de impresión pendientes y pedidos en producción.</li>
</ul>
<div class="ok">
    <b>Para qué sirve:</b> entrás a la mañana, mirás el tablero y sabés qué está corriendo, cuándo
    entregás y qué tenés que ir a comprar antes de quedarte sin material.
</div>

<div class="pagebreak"></div>

<!-- DESCUENTO -->
<h2>16. ¿Cuándo se descuenta el material?</h2>
<p>El material se descuenta del inventario <b>cuando cada pieza se imprime</b> (cuando marcás el
   trabajo como "Impreso"), no cuando aprobás el presupuesto.</p>
<h3>Por qué es así</h3>
<p>Porque es lo que pasa de verdad: el filamento se gasta al imprimir, no al aceptar el pedido.
   Y porque hace que el aviso de "comprar materia prima" del tablero sea correcto. Si el stock se
   restara al aprobar, el tablero contaría el mismo material dos veces (una al aprobar y otra al
   proyectar la cola) y te diría que falta más de lo que falta en realidad.</p>
<div class="box">
    <b>En resumen:</b> aprobar = arma la cola y te <i>avisa</i> si el stock no alcanza.
    Imprimir (marcar "Impreso") = descuenta el material de verdad y queda registrado como
    movimiento de "Producción".
</div>

<!-- FLUJO -->
<h2>17. Flujo de trabajo recomendado</h2>
<ul>
    <li><span class="step">1.</span> Cargá tus <b>filamentos</b> y <b>agregados</b> con su costo y su stock mínimo.</li>
    <li><span class="step">2.</span> Cargá el stock inicial con <b>Ajustes manuales</b> o <b>Compras</b>.</li>
    <li><span class="step">3.</span> Revisá que tus <b>máquinas</b> estén bien marcadas (cuáles son multicolor).</li>
    <li><span class="step">4.</span> Creá un <b>producto</b> por cada pieza que vendés, con su costo, precio y si es multicolor.</li>
    <li><span class="step">5.</span> Cuando un cliente pide, armá un <b>presupuesto</b> y enviale el <b>PDF</b>.</li>
    <li><span class="step">6.</span> Cuando acepta, <b>aprobá</b> el presupuesto: se arma la cola y obtenés la entrega estimada.</li>
    <li><span class="step">7.</span> A medida que imprimís, marcá cada trabajo como <b>Imprimiendo</b> y luego <b>Impreso</b> (ahí se descuenta el material).</li>
    <li><span class="step">8.</span> Mirá el <b>Tablero</b> cada día para ver producción, entregas y qué comprar.</li>
    <li><span class="step">9.</span> Repuestás materiales con <b>Compras</b> confirmadas; la <b>campanita</b> te avisa cuando algo está bajo.</li>
</ul>

</body>
</html>
"""


def main():
    logo = logo_data_uri()
    logo_tag = f'<img src="{logo}" />' if logo else ""
    html = HTML.format(logo=logo_tag)

    buffer = BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer, encoding="utf-8")
    if result.err:
        raise RuntimeError("No se pudo generar el PDF instructivo.")

    OUTPUT_PATH.write_bytes(buffer.getvalue())
    print(f"PDF generado en: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
