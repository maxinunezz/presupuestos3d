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

    /* CAJA de EJEMPLO */
    .ej {{ background: #f7f7f7; border: 1px dashed #888; padding: 9px 12px; margin: 9px 0; font-size: 9.8pt; }}
    .ej .tag {{ background: #000; color: #fff; font-size: 7.5pt; font-weight: bold; padding: 2px 6px; letter-spacing: 0.5px; }}

    table {{ width: 100%; border-collapse: collapse; margin: 9px 0; font-size: 9.5pt; }}
    th {{ background: #000; color: #fff; text-align: left; padding: 6px 8px; }}
    td {{ border-bottom: 1px solid #ccc; padding: 6px 8px; vertical-align: top; background: #fff; }}

    .step {{ font-weight: bold; color: #000; }}
    .footer {{ color: #999; font-size: 8.5pt; text-align: center; border-top: 1px solid #e5e5e5; padding-top: 5px; }}
    .pagebreak {{ page-break-before: always; }}

    /* INDICE */
    .toc {{ list-style: none; margin: 6px 0 0 0; padding: 0; }}
    .toc li {{ margin: 0; padding: 6px 4px; border-bottom: 1px solid #e5e5e5; font-size: 10.3pt; }}
    .toc .tn {{ display: inline-block; width: 26px; color: #000; font-weight: bold; }}

    /* GRAFICO PLANO de ejemplo (la unica parte a color, como en la pantalla real) */
    .chart-frame {{ border: 1px solid #cfcfcf; padding: 11px 14px 13px 14px; margin: 11px 0; }}
    .chart-title {{ font-weight: bold; font-size: 10pt; margin: 0 0 9px 0; color: #000; }}
    .chart {{ width: 100%; border-collapse: collapse; margin: 0; }}
    .chart td {{ border: none; padding: 2px 6px; background: #fff; font-size: 9pt; }}
    .chart td.cl {{ width: 38%; text-align: right; color: #000; }}
    .chart-note {{ font-size: 8.5pt; color: #666; margin: 9px 0 0 0; }}
    .legend {{ border-collapse: collapse; margin: 9px 0 4px 0; }}
    .legend td {{ border: 2px solid #fff; padding: 5px 14px; color: #fff; font-size: 8.5pt; font-weight: bold; text-align: center; }}
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
    <span class="cs">Costeo · Presupuestos · Inventario · Producción · Métricas · Gastos</span>
</div>
<div class="cover-rule"></div>
<p class="cover-lead">
    Guía completa de uso del sistema de gestión de 3darg.<br/>
    Con ejemplos concretos en cada sección, pensada para que cualquier<br/>
    persona, sin haberlo visto antes, lo maneje de punta a punta.
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
    <li><span class="tn">10</span> Costeo de productos (y piezas)</li>
    <li><span class="tn">11</span> Stock de piezas</li>
    <li><span class="tn">12</span> Stock de productos terminados</li>
    <li><span class="tn">13</span> Presupuestos</li>
    <li><span class="tn">14</span> ¿Cuándo se descuenta el material?</li>
    <li><span class="tn">15</span> Máquinas (impresoras)</li>
    <li><span class="tn">16</span> Trabajos de producción (y reimpresión)</li>
    <li><span class="tn">17</span> Historial de impresiones por máquina</li>
    <li><span class="tn">18</span> Cola de producción</li>
    <li><span class="tn">19</span> Tablero de producción</li>
    <li><span class="tn">20</span> Métricas (KPIs del negocio)</li>
    <li><span class="tn">21</span> Gastos (panel de gastos)</li>
    <li><span class="tn">22</span> Selector de idioma (Español / English)</li>
    <li><span class="tn">23</span> Flujo de trabajo recomendado</li>
</ul>

<div class="pagebreak"></div>

<!-- INTRO -->
<h2>1. Qué es este sistema y cómo entrar</h2>
<p class="lead">
    Este panel maneja todo el circuito de tu negocio de impresión 3D en un solo lugar: cargás
    tus materiales y su stock, registrás compras, costeás cada producto (y sus piezas), armás
    presupuestos para el cliente y, cuando los aprobás, el sistema descuenta el material, arma
    solo la <b>cola de producción</b> de tus impresoras y te dice <b>cuándo entregás</b> y
    <b>qué materia prima vas a tener que comprar</b>. Además llevás tus <b>gastos</b> del negocio.
</p>
<p>Para entrar abrís el navegador en la dirección del panel y, al estar adentro, vas a ver en la
   página de inicio una lista de secciones agrupadas en bloques:</p>
<ul>
    <li><b>Inventario:</b> Filamentos, Agregados, Totales de inventario, Compras, Movimientos de
        stock y Ajustes manuales de stock.</li>
    <li><b>Costeo y presupuestos:</b> Costeo de productos, Stock de piezas, Stock de productos
        terminados, Presupuestos y Métricas.</li>
    <li><b>Producción:</b> Máquinas, Trabajos de producción, Cola de producción y Tablero de
        producción.</li>
    <li><b>Gastos:</b> Gastos, Topes y Panel de gastos.</li>
</ul>
<div class="box">
    <b>Cómo se navega:</b> hacés clic en una sección para ver su lista. Arriba a la derecha de
    cada lista hay un botón "Agregar" para crear un registro nuevo. Hacés clic en cualquier fila
    para abrirla y editarla. Casi todas las listas tienen un <b>buscador</b> y <b>filtros</b> a
    la derecha para encontrar lo que necesitás.
</div>
<div class="ej">
    <span class="tag">EJEMPLO</span> Querés ver qué filamentos están por agotarse: entrás a
    <b>Filamentos</b>, hacés clic en el filtro <b>"Bajo stock: Sí"</b> de la derecha y la lista te
    deja solo los que están por debajo del mínimo. O más rápido todavía: hacés clic en la
    <b>campanita</b> 🔔 de arriba y te lleva directo a esa misma lista.
</div>

<!-- REGLA DE ORO -->
<h2>2. Cómo cambia el stock (regla de oro)</h2>
<p>El stock de cada material <b>no se escribe a mano</b> en la ficha del material. Cambia solo
   por vías controladas, para que siempre quede registrado <i>por qué</i> cambió cada cantidad:</p>
<table>
    <tr><th>Vía</th><th>Efecto</th></tr>
    <tr><td><b>Compra confirmada</b></td><td>Suma stock (y actualiza el costo si cambió el precio).</td></tr>
    <tr><td><b>Ajuste manual</b></td><td>Suma o resta para corregir diferencias con la realidad.</td></tr>
    <tr><td><b>Aprobar un presupuesto</b></td><td>Resta el material (filamento + agregados) de todo lo que hay que producir.</td></tr>
    <tr><td><b>Cancelar un pedido</b></td><td>Devuelve al stock el material que todavía no se usó.</td></tr>
    <tr><td><b>Reimpresión por falla</b></td><td>Pierde lo que se gastó en la impresión fallida; el resto vuelve al stock.</td></tr>
</table>
<div class="box">
    <b>Lo más importante (y el cambio respecto de antes):</b> el material se descuenta <b>al
    aprobar el presupuesto</b>, no al imprimir. Apenas aceptás un pedido, el stock baja por todo
    lo que vas a necesitar. El porqué está explicado en el punto 14.
</div>
<div class="ej">
    <span class="tag">EJEMPLO</span> Comprás 5 rollos de PLA Negro de 1 kg y confirmás la compra:
    el stock de PLA Negro sube <b>+5.000 g</b> y queda un movimiento "Compra +5.000 g". Después
    aprobás un pedido que usa 250 g: el stock baja <b>−250 g</b> en ese mismo momento, con un
    movimiento de "Producción (aprobación)". En ningún momento tocaste el número de stock a mano.
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
<div class="ej">
    <span class="tag">EJEMPLO</span> Tenés PLA Cian con <b>150 g</b> de stock y su mínimo está en
    <b>500 g</b>. Como 150 &lt; 500, la campanita muestra un <b>1</b> en rojo y, al pasar el mouse,
    lista "PLA Cian — 150 g (mínimo 500 g)". Si además un agregado está bajo, el número pasa a
    <b>2</b>, y así.
</div>
<div class="box">
    <b>Tip:</b> el mínimo de los agregados se mide en la <b>misma unidad</b> que el agregado
    (unidades para pelotas, gramos para argollas que van por peso). Poné el mínimo en el número
    que te dé tranquilidad para no quedarte sin material en medio de un pedido.
</div>

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
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Una ficha de filamento típica:</div>
<table>
    <tr><th>Marca</th><th>Tipo</th><th>Color</th><th>Costo por kilo</th><th>Stock</th><th>Mínimo</th><th>Estado</th></tr>
    <tr><td>Grilon3</td><td>PLA</td><td>Negro</td><td>$18.000</td><td>3.200 g</td><td>1.000 g</td><td>OK</td></tr>
    <tr><td>Grilon3</td><td>PLA</td><td>Cian</td><td>$18.000</td><td>150 g</td><td>500 g</td><td>BAJO</td></tr>
</table>
<p>El sistema calcula solo el costo por gramo ($18.000 ÷ 1000 = <b>$18 por gramo</b>), que después
   usa para costear cada pieza.</p>
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
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Algunos agregados típicos:</div>
<table>
    <tr><th>Nombre</th><th>Unidad</th><th>Costo</th><th>Stock</th><th>Mínimo</th></tr>
    <tr><td>Argolla llavero</td><td>Unidad</td><td>$35</td><td>480 u</td><td>200 u</td></tr>
    <tr><td>Imán 8 mm</td><td>Unidad</td><td>$90</td><td>120 u</td><td>150 u</td></tr>
    <tr><td>Caja de cartón chica</td><td>Unidad</td><td>$210</td><td>60 u</td><td>30 u</td></tr>
</table>
<p>En este ejemplo el imán está por debajo del mínimo (120 &lt; 150), así que aparece en la campanita.</p>

<!-- TOTALES -->
<h2>6. Totales de inventario</h2>
<p>Vista de solo lectura para ver de un vistazo cuánto valor tenés inmovilizado en stock. Suma
   filamentos y agregados valorizados según su costo actual. Sirve para saber cuánta plata
   tenés "guardada" en materiales sin sumar a mano.</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> Si tenés 12 kg de filamento valorizados en <b>$216.000</b> y
    agregados (argollas, imanes, cajas) por <b>$40.000</b>, la pantalla de Totales te muestra
    <b>$256.000</b> inmovilizados en inventario.
</div>

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
<div class="ej">
    <span class="tag">EJEMPLO</span> &nbsp;Le comprás a tu proveedor "Filamentos SA":<br/>
    &nbsp;&nbsp;–&nbsp; Línea 1: PLA Negro, 3 rollos = <b>3.000 g</b>, precio $19.000 por kilo (antes era $18.000).<br/>
    &nbsp;&nbsp;–&nbsp; Línea 2: Argolla llavero, <b>200 u</b>, precio $35 c/u.<br/>
    Al confirmar: el PLA Negro sube +3.000 g <b>y</b> su costo pasa a $19.000/kg; las argollas suben
    +200 u. Quedan dos movimientos de "Compra" en el historial. Total de la compra:
    3 × $19.000 + 200 × $35 = <b>$64.000</b>.
</div>
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
    <tr><td>Producción (aprobación)</td><td>Se aprobó un presupuesto y se descontó su material (resta).</td></tr>
    <tr><td>Cancelación de pedido</td><td>Se canceló un pedido aprobado y volvió el material no usado (suma).</td></tr>
    <tr><td>Reimpresión por falla</td><td>Una impresión salió mal; vuelve al stock el material no perdido (suma).</td></tr>
    <tr><td>Ajuste manual</td><td>Corregiste el stock a mano (suma o resta).</td></tr>
</table>
<p>Esta vista es de lectura: no se edita acá, sirve para auditar y entender por qué cambió el stock.</p>
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Una semana cualquiera en el historial de PLA Negro:</div>
<table>
    <tr><th>Fecha</th><th>Material</th><th>Cantidad</th><th>Motivo</th></tr>
    <tr><td>02/06</td><td>PLA Negro</td><td>+3.000 g</td><td>Compra</td></tr>
    <tr><td>04/06</td><td>PLA Negro</td><td>−250 g</td><td>Producción (aprobación)</td></tr>
    <tr><td>05/06</td><td>PLA Negro</td><td>+120 g</td><td>Cancelación de pedido</td></tr>
    <tr><td>06/06</td><td>PLA Negro</td><td>+50 g</td><td>Ajuste manual</td></tr>
</table>

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
<div class="ej">
    <span class="tag">EJEMPLO</span> Abrís un rollo nuevo y el sistema marca 0 g, pero en realidad
    entraron 1.000 g: cargás un ajuste de <b>+1.000</b> con nota "carga inicial rollo PLA Blanco".
    Otro caso: se te cayó y rompió medio rollo, cargás <b>−400</b> con nota "rollo dañado". En los
    ajustes el stock nunca queda en negativo: si restás de más, queda en 0.
</div>
<div class="box">
    <b>Tip:</b> si querés dejar el stock en un valor exacto, fijate cuánto hay hoy y cargá la
    diferencia. Los ajustes nunca dejan el stock en negativo (la producción sí puede, ver punto 14).
</div>

<!-- COSTEO DE PRODUCTOS -->
<h2>10. Costeo de productos (y piezas)</h2>
<p>Acá definís cuánto te cuesta y a cuánto vendés <b>un producto</b>. Un producto se arma con
   una o varias <b>piezas</b> (las partes físicas que imprimís), más los agregados, la mano de
   obra y el margen. El sistema suma todo y calcula solo el precio de venta final.</p>
<h3>Qué cargás en cada producto</h3>
<ul>
    <li><b>Datos:</b> nombre, descripción, si está activo (solo los activos se usan en
        presupuestos) y la <b>prioridad</b> (Alta / Media / Baja / Sin prioridad).</li>
    <li><b>Piezas:</b> cada pieza lleva los <b>gramos de filamento</b> por corrida, las
        <b>horas de impresión</b> por corrida, cuántas <b>unidades</b> entran por producto y
        cuántas <b>salen por corrida del gcode</b>. Una pieza con más de un color de filamento
        queda marcada como <b>multicolor (AMS)</b> automáticamente.</li>
    <li><b>Agregados:</b> los insumos no-filamento que lleva el producto (argollas, imanes, etc.).</li>
    <li><b>Impresión y máquina:</b> costo de máquina por hora y % de merma (material que se desperdicia).</li>
    <li><b>Mano de obra / post-proceso:</b> horas de post-proceso por producto y costo por hora
        (armado, lijado, pintado, pegado de agregados, etc.).</li>
    <li><b>Precio:</b> margen de ganancia (%) y redondeo.</li>
    <li><b>Archivo del modelo:</b> opcional, podés guardar el gcode y subir el archivo .3mf/.stl.</li>
</ul>
<div class="box">
    <b>Prioridad:</b> ordena la cola de producción. Los productos de prioridad <b>Alta</b> entran
    a imprimirse antes que los de <b>Media</b> y <b>Baja</b>; "Sin prioridad" va al final. Es la
    clave de orden del scheduler, la cola y el tablero.
</div>
<div class="box">
    <b>Multicolor (AMS):</b> si alguna pieza usa varios colores en simultáneo, el producto queda
    multicolor. El sistema solo va a poder mandarlo a una impresora con AMS (las Bambu Lab), nunca
    a la Ender. Más detalle en el punto 15.
</div>
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Costeo de un <b>Llavero con logo</b> (1 pieza, 12 g, 1 argolla):</div>
<table>
    <tr><th>Concepto (cálculo)</th><th>Subtotal</th></tr>
    <tr><td>Material — filamento: 12 g × $18/g</td><td>$216,00</td></tr>
    <tr><td>Merma 5% — $216 × 5%</td><td>$10,80</td></tr>
    <tr><td>Agregado — argolla: 1 u × $35</td><td>$35,00</td></tr>
    <tr><td>Máquina — 0,4 h × $500/h</td><td>$200,00</td></tr>
    <tr><td>Mano de obra — 0,1 h × $1.500/h</td><td>$150,00</td></tr>
    <tr><td><b>Costo total por pieza</b></td><td><b>$611,80</b></td></tr>
    <tr><td>Margen 60% — $611,80 × 1,60</td><td>$978,88</td></tr>
    <tr><td><b>Precio de venta (redondeo a $50)</b></td><td><b>$1.000</b></td></tr>
</table>
<h3>Resumen de costos</h3>
<p>Al guardar, el producto muestra un desglose: material (+ merma), agregados, máquina, mano de
   obra, costo total por producto, margen aplicado y <b>precio de venta final</b>. Ese desglose es
   interno: el cliente nunca lo ve.</p>

<!-- STOCK DE PIEZAS -->
<h2>11. Stock de piezas</h2>
<p>Es una pantalla de solo lectura que muestra el <b>stock de piezas ya impresas</b>: las partes
   que tenés impresas y guardadas, listas para usar sin volver a la impresora. Cada pieza tiene
   su <b>stock</b> y su <b>mínimo</b>, igual que los materiales.</p>
<h3>De dónde sale ese stock</h3>
<ul>
    <li>De la <b>sobrante de gcode</b>: si una corrida saca más piezas de las que pedías, las de
        más se suman al stock de esa pieza cuando marcás el trabajo como Impreso.</li>
    <li>De <b>pedidos cancelados</b>: las piezas que ya estaban impresas vuelven al stock de piezas.</li>
</ul>
<h3>Para qué sirve</h3>
<p>Al aprobar un pedido, el sistema usa primero las piezas que tenés en stock y solo manda a
   imprimir lo que falta. Eso te ahorra impresiones y tiempo de máquina.</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> Un llavero sale de a 4 por corrida pero solo necesitabas 2:
    las otras 2 quedan en el <b>stock de piezas</b>. La próxima vez que aprobás un pedido con
    2 llaveros, el sistema los toma de ese stock y <b>no imprime nada</b>.
</div>

<!-- STOCK DE PRODUCTOS TERMINADOS -->
<h2>12. Stock de productos terminados</h2>
<p>Es la pantalla de solo lectura del <b>stock de productos ya armados</b>: terminados, listos
   para entregar sin imprimir ni armar nada. Compara el stock de cada producto con su mínimo.</p>
<h3>De dónde sale</h3>
<p>Principalmente de los <b>pedidos para stock</b> (ver punto 13): cuando completás un pedido de
   reposición interna, cada producto terminado se suma a este stock.</p>
<h3>Para qué sirve</h3>
<p>Cuando entra un pedido de cliente, el sistema <b>sirve primero del stock de terminados</b>: esa
   parte no se produce, no consume piezas ni material, y queda lista para entregar.</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> Tenés 10 llaveros terminados en stock. Entra un pedido de
    cliente por 3 llaveros: el sistema descuenta 3 del stock de terminados, no genera ninguna
    impresión y el pedido queda <b>listo para entregar</b> (ver punto 13).
</div>

<!-- PRESUPUESTOS -->
<h2>13. Presupuestos</h2>
<p>Un presupuesto es la cotización para un cliente. Agrupa varios productos ya costeados, cada
   uno con su cantidad, y genera el PDF para enviar.</p>
<h3>Cómo armar un presupuesto</h3>
<ul>
    <li><span class="step">1.</span> "Agregar presupuesto": cargá el nombre del cliente y una descripción.</li>
    <li><span class="step">2.</span> Agregá los productos (solo activos) con su cantidad. El precio sale del producto, pero podés sobreescribirlo.</li>
    <li><span class="step">3.</span> Si hay un <b>costo fijo</b> (envío, diseño, etc.) cargalo aparte. Ajustá el redondeo del total.</li>
    <li><span class="step">4.</span> Guardá: vas a ver el resumen con subtotal, costo fijo, total y cantidad de piezas.</li>
</ul>
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Presupuesto para el cliente <b>"Estudio Belgrano"</b>:</div>
<table>
    <tr><th>Producto</th><th>Cantidad</th><th>Precio unit.</th><th>Subtotal</th></tr>
    <tr><td>Llavero con logo</td><td>2</td><td>$1.000</td><td>$2.000</td></tr>
    <tr><td>Maceta hexagonal</td><td>1</td><td>$4.500</td><td>$4.500</td></tr>
    <tr><td colspan="3"><b>Subtotal productos</b></td><td><b>$6.500</b></td></tr>
    <tr><td colspan="3">Costo fijo (envío)</td><td>$1.200</td></tr>
    <tr><td colspan="3"><b>Total (redondeo a $100)</b></td><td><b>$7.700</b></td></tr>
</table>
<p>Ese precio unitario queda <b>congelado</b> en el presupuesto: aunque después cambies el costeo
   del producto, este presupuesto mantiene los $1.000 y $4.500.</p>
<h3>Estados del presupuesto</h3>
<p>Borrador → Enviado → Aprobado → En producción → Completado (o Cancelado). El estado te ayuda
   a seguir en qué etapa está cada pedido. Cada cambio importante queda registrado con su fecha
   (enviado, aprobado, inicio y fin de producción, completado).</p>
<div class="box">
    <b>El estado se sincroniza solo con la producción:</b> cuando una pieza del pedido empieza a
    imprimirse, el presupuesto pasa a "En producción"; cuando se terminan todas, pasa a
    "Completado". No tenés que cambiarlo a mano (aunque podés, con el selector de estado).
</div>
<h3>Aprobar (descuenta material y arma la cola)</h3>
<div class="ok">
    <b>Al aprobar un presupuesto, el sistema:</b><br/>
    &nbsp;&nbsp;–&nbsp; Sirve lo que pueda del <b>stock de productos terminados</b> y del <b>stock de piezas</b>.<br/>
    &nbsp;&nbsp;–&nbsp; <b>Descuenta el material</b> (filamento + agregados) de todo lo que sí hay que producir.<br/>
    &nbsp;&nbsp;–&nbsp; Crea un <b>trabajo de producción por cada pieza</b> que falta imprimir y le recomienda una impresora (respetando AMS y prioridad).<br/>
    &nbsp;&nbsp;–&nbsp; Calcula la <b>fecha de entrega estimada</b>.<br/>
    &nbsp;&nbsp;–&nbsp; Te <b>avisa</b> si el stock no alcanzaba (el faltante queda visible para comprar).<br/>
    <br/>
    Usá la acción "Aprobar presupuesto(s) y generar cola de producción" desde la lista.
</div>
<h3>Pedido para stock (sin cliente)</h3>
<p>Si marcás <b>"Para stock"</b>, el pedido es una reposición de stock interno, no para un
   cliente. Se aprueba, imprime y completa igual; la diferencia es que al <b>completarlo</b> cada
   producto terminado se suma al <b>stock de productos terminados</b> (punto 12). Si no le ponés
   nombre de cliente, queda como "Reposición de stock".</p>
<h3>Pedido listo para entregar (sin producción)</h3>
<p>Si un pedido de cliente se sirve <b>100% del stock</b> (de terminados o de piezas) y no genera
   ninguna impresión, el sistema lo marca como <b>"Listo para entregar"</b>. Lo vas a ver con la
   columna «Sin producción ✓» y lo cerrás con la acción masiva «Marcar como entregado».</p>
<h3>Producción y entrega</h3>
<p>Dentro del presupuesto vas a ver a qué máquina fue cada pieza, las horas de impresión, las horas
   de post-proceso y la <b>entrega estimada</b>. La fecha se calcula sola, pero si la <b>editás a
   mano</b> queda fija y el sistema no te la pisa al recalcular la cola.</p>
<h3>PDF para el cliente</h3>
<p>Cada presupuesto tiene un botón <b>"PDF cliente"</b>. Ese PDF muestra solo lo que el cliente
   debe ver: producto, cantidad, precio unitario y total. <b>Nunca</b> muestra el desglose
   interno de costos ni la cola de producción.</p>

<!-- DESCUENTO -->
<h2>14. ¿Cuándo se descuenta el material?</h2>
<p>El material se descuenta del inventario <b>cuando aprobás el presupuesto</b>, no cuando
   imprimís. Apenas el cliente acepta y aprobás, el stock baja por todo lo que vas a producir.</p>
<h3>Cómo es el orden de consumo al aprobar</h3>
<ul>
    <li><span class="step">1.</span> Primero sirve del <b>stock de productos terminados</b> (no se produce nada de eso).</li>
    <li><span class="step">2.</span> De lo que queda, usa el <b>stock de piezas</b> ya impresas (esas tampoco se imprimen).</li>
    <li><span class="step">3.</span> Lo que todavía falta se manda a imprimir y se le <b>descuenta el filamento</b> ahí mismo. Los <b>agregados</b> se descuentan a nivel producto.</li>
</ul>
<h3>Por qué es así</h3>
<p>Porque hace que el consumo impacte de inmediato en las métricas de inventario y costos, y que
   el aviso de "comprar materia prima" sea correcto: una vez aprobado, el sistema mira el stock
   real (que ya bajó) y te lista todo lo que quedó por debajo del mínimo, sin contar el material
   dos veces.</p>
<div class="warn">
    <b>El stock de producción puede quedar en negativo.</b> Si aprobás más de lo que tenés, el
    stock queda en negativo a propósito: así el faltante queda visible y el tablero te dice
    exactamente cuánto comprar. (Los ajustes y compras nunca dejan negativo; la producción sí.)
</div>
<div class="ej">
    <span class="tag">EJEMPLO</span> Aprobás un pedido de 10 macetas (300 g c/u = 3.000 g) y tenés
    2.500 g de PLA. Al aprobar, el stock pasa a <b>−500 g</b> y el tablero te avisa "PLA: faltan
    500 g". Imprimir cada maceta ya <b>no</b> vuelve a descontar: el material se descontó al aprobar.
</div>
<div class="box">
    <b>Si cancelás un pedido aprobado</b>, el sistema te <b>devuelve</b> el material que todavía
    no se usó: el filamento de los trabajos no impresos, las piezas que se habían tomado del stock,
    las piezas ya impresas (vuelven al stock de piezas) y los agregados. No revierte pedidos ya
    completados.
</div>

<!-- MAQUINAS -->
<h2>15. Máquinas (impresoras)</h2>
<p>Es el listado de tus impresoras. Cada máquina activa procesa su propia cola de trabajos, así
   que varias piezas se imprimen en paralelo. Hoy tenés tres: dos Bambu Lab A1 Combo y una
   Ender 3 V3 Plus.</p>
<h3>Qué guarda cada máquina</h3>
<ul>
    <li><b>Activa:</b> si está inactiva, no se le asignan trabajos nuevos ni cuenta para la cola.</li>
    <li><b>Imprime multicolor (AMS):</b> marca si la máquina puede imprimir varios colores en
        simultáneo. Las Bambu Lab tienen AMS (sí); la Ender no.</li>
    <li><b>Costo por hora:</b> el costo horario de esa máquina (lo cargás vos), que se usa para
        costear y para calcular la depreciación.</li>
    <li><b>Horas impresas y depreciación:</b> el sistema acumula las horas impresas de la máquina
        (de solo lectura) y muestra la <b>depreciación acumulada</b> = horas impresas × costo por hora.</li>
    <li><b>Historial de impresiones:</b> dentro de cada máquina ves su historial (punto 17).</li>
</ul>
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Tu parque de máquinas:</div>
<table>
    <tr><th>Máquina</th><th>Activa</th><th>AMS</th><th>$/hora</th><th>Horas impresas</th><th>Depreciación</th></tr>
    <tr><td>Bambu Lab A1 Combo #1</td><td>Sí</td><td>Sí</td><td>$500</td><td>78 h</td><td>$39.000</td></tr>
    <tr><td>Bambu Lab A1 Combo #2</td><td>Sí</td><td>Sí</td><td>$500</td><td>51 h</td><td>$25.500</td></tr>
    <tr><td>Ender 3 V3 Plus</td><td>Sí</td><td>No</td><td>$350</td><td>13,5 h</td><td>$4.725</td></tr>
</table>
<div class="box">
    <b>Cómo se respeta el multicolor:</b> si un producto es multicolor, el sistema solo lo manda a
    una máquina con AMS. Aunque las dos Bambu estén ocupadas, nunca lo manda a la Ender: lo deja
    "sin máquina" para que vos decidas. Si intentás asignarlo a mano a la Ender, el panel lo rechaza.
</div>
<div class="warn">
    <b>Si desactivás una máquina</b> (por ejemplo, porque se rompió), sus trabajos pendientes no
    se pierden: el sistema los <b>libera</b> (quedan "sin máquina") y te avisa para que los
    reasignes. Vas a verlos en el Tablero y en la Cola, en "Trabajos sin máquina asignada".
</div>

<!-- TRABAJOS -->
<h2>16. Trabajos de producción (y reimpresión)</h2>
<p>Un trabajo es <b>una pieza de un presupuesto</b> (con su cantidad) asignada a una máquina y
   con una posición en la cola de esa máquina. Un mismo presupuesto suele tener varios trabajos
   repartidos en distintas impresoras.</p>
<h3>Qué podés hacer</h3>
<ul>
    <li>Desde la lista podés cambiar directo la <b>máquina</b>, el <b>orden</b> en la cola y el <b>estado</b>.</li>
    <li>Estados: <b>En cola</b> → <b>Imprimiendo</b> → <b>Impreso</b> (o <b>Cancelado</b>).</li>
    <li>Al marcar <b>Imprimiendo</b>, se registra el inicio real.</li>
    <li>Al marcar <b>Impreso</b>: el material <b>ya se descontó al aprobar</b>, así que acá NO se
        vuelve a descontar. Lo que sí pasa: si la corrida sacó piezas de más, la <b>sobrante se
        suma al stock de piezas</b>, y se registra en el historial de la máquina.</li>
    <li>Cualquier cambio de máquina, orden o estado <b>recalcula la cola</b> y las fechas estimadas.</li>
</ul>
<div class="ej">
    <span class="tag">EJEMPLO</span> El pedido de "Estudio Belgrano" (2 llaveros + 1 maceta) genera
    al aprobarse trabajos por pieza: "Llavero ×2" a la Bambu #1 y "Maceta ×1" a la Ender. El
    filamento ya se descontó al aprobar. Cuando arrancás el llavero lo marcás <b>Imprimiendo</b>;
    al sacarlo de la cama lo marcás <b>Impreso</b> y queda registrado en el historial de la Bambu #1.
</div>
<h3>Reimpresión por falla (impresión obsoleta)</h3>
<p>Si una impresión sale mal, usás la acción <b>«Marcar impresión obsoleta (reimprimir)»</b>. El
   trabajo vuelve a la cola para reimprimirse. Como el filamento ya se había descontado al aprobar,
   se te pide cargar <b>cuántos gramos se perdieron</b> en la impresión fallida: eso se pierde de
   verdad y el resto vuelve al stock. Los <b>agregados no se tocan</b>. Al reimprimirse, el sistema
   vuelve a contar el material de la pieza buena.</p>
<div class="box">
    <b>Ventana de carga:</b> las impresoras imprimen de corrido (un trabajo puede cruzar la noche),
    pero un trabajo nuevo solo <b>arranca</b> entre las 07:00 y las 23:00, porque alguien tiene que
    cargar la pieza. Si una máquina queda libre de madrugada, el siguiente arranca a las 07:00.
</div>

<!-- HISTORIAL -->
<h2>17. Historial de impresiones por máquina</h2>
<p>Cada máquina lleva su propio <b>historial de impresiones</b>: una lista de solo lectura, dentro
   de la ficha de la máquina, con todo lo que pasó por esa impresora. Te sirve para saber qué
   imprimió cada máquina, cuándo y en qué cantidad.</p>
<h3>Qué guarda cada registro</h3>
<ul>
    <li><b>Título</b> descriptivo (producto/pieza, cantidad y pedido al que pertenece).</li>
    <li><b>Cantidad</b> y <b>horas de impresión</b>.</li>
    <li><b>Estado:</b> <b>Impreso</b> (terminó bien) o <b>Cancelado</b> (se canceló el trabajo o el pedido).</li>
    <li><b>Fecha de finalización.</b></li>
</ul>
<p>El historial se arma solo: se agrega un registro cuando marcás un trabajo como <b>Impreso</b> y
   también cuando se <b>cancela</b> (sea el trabajo suelto o el pedido entero). Es una foto: aunque
   después borres o cambies el trabajo, el historial de la máquina queda. Está ordenado de lo más
   nuevo a lo más viejo.</p>
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Historial de la Bambu Lab A1 Combo #1:</div>
<table>
    <tr><th>Título</th><th>Cant.</th><th>Horas</th><th>Estado</th><th>Finalizado</th></tr>
    <tr><td>Llavero con logo ×2 — Pedido #18 (Estudio Belgrano)</td><td>2</td><td>0,80</td><td>Impreso</td><td>26/06 15:10</td></tr>
    <tr><td>Maceta hexagonal ×1 — Pedido #17 (Casa Norte)</td><td>1</td><td>3,20</td><td>Cancelado</td><td>25/06 11:40</td></tr>
    <tr><td>Soporte de celular ×5 — Pedido #15 (Kiosco 24h)</td><td>5</td><td>2,50</td><td>Impreso</td><td>24/06 18:05</td></tr>
</table>

<!-- COLA -->
<h2>18. Cola de producción</h2>
<p>Vista de solo lectura con la cola de cada máquina: qué trabajos tiene, en qué orden, con su
   inicio y fin de impresión estimados, y cuándo queda libre cada impresora. Abajo muestra la
   <b>cola total</b> de todos los trabajos ordenados por inicio. Sirve para ver el panorama de
   carga de todas las máquinas de un vistazo.</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> La Bambu #1 muestra: "Llavero ×2 — arranca hoy 14:00, termina
    14:48", "Soporte ×5 — arranca 14:48, termina 17:20", y abajo "Máquina libre a partir de las
    17:20". Así sabés que si entra un pedido nuevo urgente a esa máquina, antes de las 17:20 no
    arranca.
</div>

<!-- TABLERO -->
<h2>19. Tablero de producción</h2>
<p>Es la pantalla "entro y me dice todo". De solo lectura, reúne en un solo lugar:</p>
<ul>
    <li><b>Qué se está imprimiendo</b> ahora en cada máquina y cuándo termina.</li>
    <li><b>Próximas entregas:</b> los presupuestos aprobados o en producción ordenados por fecha de entrega.</li>
    <li><b>Comprar materia prima:</b> como el material ya se descontó al aprobar, el tablero mira el
        <b>stock real</b> y te lista todo lo que quedó <b>por debajo del mínimo</b> (o en negativo),
        cuánto falta comprar y <b>cuándo</b> lo vas a necesitar en la máquina.</li>
    <li>Indicadores rápidos: trabajos en cola, horas de impresión pendientes y pedidos en producción.</li>
</ul>
<div class="ej">
    <span class="tag">EJEMPLO</span> Entrás un lunes a la mañana y el tablero te dice: Bambu #1
    imprimiendo "Maceta ×3" (termina 11:20); próxima entrega "Estudio Belgrano" para el 27/06; y en
    <b>Comprar materia prima</b>: "PLA Negro: faltan 800 g, lo necesitás el 26/06". Sabés que tenés
    que comprar PLA Negro antes del martes.
</div>
<div class="ok">
    <b>Para qué sirve:</b> entrás a la mañana, mirás el tablero y sabés qué está corriendo, cuándo
    entregás y qué tenés que ir a comprar antes de quedarte sin material.
</div>

<!-- METRICAS -->
<h2>20. Métricas (KPIs del negocio)</h2>
<p>Es el panel de indicadores del negocio por período. Te muestra cómo venís en <b>ventas</b>,
   <b>producción</b> e <b>inventario</b>, con números y gráficos, para tomar decisiones (cuánto
   facturaste, qué se vende más, cuánto imprimís, cuánto gastás en material).</p>
<h3>Cómo leer la pantalla</h3>
<ul>
    <li>Arriba elegís el período con las pestañas <b>Semana</b>, <b>Mes</b> o <b>Año</b>. Al lado
        ves el <b>rango de fechas</b> exacto que estás mirando.</li>
    <li>El botón verde <b>"Descargar Excel"</b> baja todo el período en una planilla de <b>5 hojas</b>:
        Resumen, Facturación, Productos, Clientes y Producción (con un gráfico nativo de Excel).</li>
    <li>Cada bloque tiene su <b>color</b> para ubicarlo rápido, y los gráficos usan una paleta de
        varios colores en vez de escala de grises.</li>
</ul>
<table class="legend">
  <tr>
    <td style="background:#1d4ed8;">VENTAS · azul</td>
    <td style="background:#15803d;">PRODUCCIÓN · verde</td>
    <td style="background:#b45309;">INVENTARIO · ámbar</td>
  </tr>
</table>

<div class="chart-frame">
  <p class="chart-title">Así se ve un gráfico en la pantalla (ejemplo: productos más vendidos del mes)</p>
  <table class="chart">
    <tr><td class="cl">Llavero con logo — 120</td><td><div style="background:#2563eb; width:288pt; height:11px;">&nbsp;</div></td></tr>
    <tr><td class="cl">Maceta hexagonal — 95</td><td><div style="background:#16a34a; width:228pt; height:11px;">&nbsp;</div></td></tr>
    <tr><td class="cl">Soporte de celular — 80</td><td><div style="background:#f59e0b; width:192pt; height:11px;">&nbsp;</div></td></tr>
    <tr><td class="cl">Figura articulada — 64</td><td><div style="background:#dc2626; width:154pt; height:11px;">&nbsp;</div></td></tr>
    <tr><td class="cl">Organizador escritorio — 50</td><td><div style="background:#7c3aed; width:120pt; height:11px;">&nbsp;</div></td></tr>
    <tr><td class="cl">Topper para torta — 38</td><td><div style="background:#0891b2; width:91pt; height:11px;">&nbsp;</div></td></tr>
  </table>
  <p class="chart-note">El gráfico de facturación se ve igual pero en barras azules; el embudo de
     estados se dibuja como una dona con un color por estado. Todos a color, no en gris.</p>
</div>

<h3>A) Ventas (azul)</h3>
<table>
    <tr><th>Indicador</th><th>Qué mide</th><th>Ejemplo (mes)</th></tr>
    <tr><td>Facturación aprobada</td><td>Suma del total de los presupuestos aprobados en el período.</td><td>$1.240.000</td></tr>
    <tr><td>Presupuestos aprobados</td><td>Cuántos se aprobaron en el período.</td><td>8</td></tr>
    <tr><td>Ticket promedio</td><td>Facturación ÷ cantidad de aprobados.</td><td>$155.000</td></tr>
    <tr><td>Conversión</td><td>De los presupuestos enviados, cuántos se aprobaron.</td><td>66,7% (8 de 12)</td></tr>
    <tr><td>Margen bruto</td><td>(Facturación − costo de lo vendido) ÷ facturación.</td><td>50%</td></tr>
    <tr><td>Tiempo de ciclo</td><td>Días promedio entre aprobar y entregar (completar).</td><td>6,5 días</td></tr>
</table>
<p>Además vas a ver tres tablas: <b>productos más vendidos</b>, <b>top clientes</b> y el <b>embudo
   de estados</b>, que es una foto de cuántos presupuestos hay <i>hoy</i> en cada estado.</p>

<h3>B) Producción (verde)</h3>
<table>
    <tr><th>Indicador</th><th>Qué mide</th><th>Ejemplo (mes)</th></tr>
    <tr><td>Piezas impresas</td><td>Suma de piezas de los trabajos terminados en el período.</td><td>320</td></tr>
    <tr><td>Horas impresas</td><td>Horas de impresión de esos trabajos terminados.</td><td>142,5 h</td></tr>
    <tr><td>Tasa de reimpresión</td><td>Reimpresiones por falla ÷ trabajos impresos.</td><td>4,7% (3 de 64)</td></tr>
    <tr><td>Cumplimiento de entrega</td><td>Entregados a tiempo ÷ entregados con fecha pactada.</td><td>87,5% (7 de 8)</td></tr>
</table>
<p>Y la tabla <b>Uso por máquina</b>, con trabajos, piezas y horas por impresora.</p>

<h3>C) Inventario y costos (ámbar)</h3>
<table>
    <tr><th>Indicador</th><th>Qué mide</th><th>Ejemplo (mes)</th></tr>
    <tr><td>Gasto en compras</td><td>Total de las compras confirmadas en el período.</td><td>$380.000 (3 compras)</td></tr>
    <tr><td>Consumo de material ($)</td><td>Valor del material descontado al aprobar pedidos.</td><td>$210.000</td></tr>
    <tr><td>Filamento consumido</td><td>Gramos de filamento descontados por producción.</td><td>11.800 g</td></tr>
    <tr><td>Insumos bajo stock mínimo</td><td>Cuántos materiales están por debajo del mínimo hoy.</td><td>2</td></tr>
</table>
<div class="box">
    <b>Qué fecha define cada métrica:</b> las ventas se miden por <b>fecha de aprobación</b>, la
    producción por <b>fin de impresión</b> y las compras por <b>fecha de confirmación</b>. El embudo
    de estados y los insumos bajo stock son una <b>foto del momento actual</b>, no del período.
</div>

<!-- GASTOS -->
<h2>21. Gastos (panel de gastos)</h2>
<p>Esta sección lleva los <b>gastos operativos y de estructura</b> del negocio: lo que NO es compra
   de insumos ni costo de producción (eso va por Inventario). Por ejemplo: alquiler, contador,
   suscripciones, publicidad, internet, etc. Sirve para saber tu <b>resultado operativo</b>.</p>
<h3>Cargar un gasto</h3>
<ul>
    <li>Cargás un <b>Gasto</b> por cada pago real, con su <b>fecha</b>.</li>
    <li><b>Categoría</b> (Administración / Comercialización / Suscripciones / IT / Otro),
        <b>concepto</b>, <b>monto</b>, proveedor y medio de pago.</li>
    <li>Si es recurrente, marcalo y elegí la <b>periodicidad</b> (Único / Mensual / Anual). El
        sistema calcula el equivalente mensual para el compromiso fijo (run-rate).</li>
</ul>
<h3>Topes</h3>
<p>En <b>Topes</b> ponés un presupuesto mensual por categoría. El panel te avisa si te pasaste.</p>
<h3>Panel de gastos</h3>
<p>Pantalla de solo lectura con <b>filtro por mes y año</b> (mes 0 = todo el año). Muestra el total
   y el desglose por categoría (tabla + torta), la evolución mensual (barras), el comparativo vs el
   período anterior, el compromiso mensual recurrente, el <b>resultado operativo</b> (ventas
   aprobadas del período − gastos) y el control de topes. También exporta a Excel.</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> En junio cargaste: alquiler $120.000 (mensual), contador
    $40.000 (mensual), publicidad $25.000 (único). El panel muestra gastos del mes <b>$185.000</b>,
    y si las ventas aprobadas fueron $1.240.000, el <b>resultado operativo</b> es $1.055.000 y los
    gastos son el <b>14,9%</b> de las ventas.
</div>

<!-- IDIOMA -->
<h2>22. Selector de idioma (Español / English)</h2>
<p>Arriba en la barra del panel hay un <b>selector de idioma</b>. Podés cambiar todo el admin entre
   <b>Español</b> y <b>English</b>: menús, botones, nombres de las secciones, ayudas y mensajes.
   El idioma queda guardado para tu usuario.</p>
<div class="box">
    <b>Nota:</b> el <b>PDF que se le manda al cliente</b> queda siempre en español, aunque tengas el
    panel en inglés. El idioma es solo para vos, para operar el sistema.
</div>

<!-- FLUJO -->
<h2>23. Flujo de trabajo recomendado</h2>
<ul>
    <li><span class="step">1.</span> Cargá tus <b>filamentos</b> y <b>agregados</b> con su costo y su stock mínimo.</li>
    <li><span class="step">2.</span> Cargá el stock inicial con <b>Ajustes manuales</b> o <b>Compras</b>.</li>
    <li><span class="step">3.</span> Revisá tus <b>máquinas</b>: cuáles son multicolor y su costo por hora.</li>
    <li><span class="step">4.</span> Creá un <b>producto</b> por cada cosa que vendés, con sus <b>piezas</b>, costo, precio y prioridad.</li>
    <li><span class="step">5.</span> Cuando un cliente pide, armá un <b>presupuesto</b> y enviale el <b>PDF</b>.</li>
    <li><span class="step">6.</span> Cuando acepta, <b>aprobá</b> el presupuesto: ahí se descuenta el material y se arma la cola con la entrega estimada.</li>
    <li><span class="step">7.</span> A medida que imprimís, marcá cada trabajo como <b>Imprimiendo</b> y luego <b>Impreso</b>. Si una sale mal, usá <b>reimprimir</b>.</li>
    <li><span class="step">8.</span> Mirá el <b>Tablero</b> cada día para ver producción, entregas y qué comprar.</li>
    <li><span class="step">9.</span> Repuestás materiales con <b>Compras</b> confirmadas; la <b>campanita</b> te avisa cuando algo está bajo.</li>
    <li><span class="step">10.</span> Cargá tus <b>gastos</b> a medida que pagás y revisá el <b>Panel de gastos</b>.</li>
    <li><span class="step">11.</span> Una vez por semana o por mes, mirá <b>Métricas</b> para ver cómo viene el negocio y bajá el Excel si querés guardarlo.</li>
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
