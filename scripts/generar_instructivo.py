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
    .toc li {{ margin: 0; padding: 7px 4px; border-bottom: 1px solid #e5e5e5; font-size: 10.5pt; }}
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
    <span class="cs">Costeo · Presupuestos · Inventario · Producción · Métricas</span>
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
    <li><span class="tn">10</span> Costeo de productos</li>
    <li><span class="tn">11</span> Presupuestos</li>
    <li><span class="tn">12</span> Máquinas (impresoras)</li>
    <li><span class="tn">13</span> Trabajos de producción</li>
    <li><span class="tn">14</span> Cola de producción</li>
    <li><span class="tn">15</span> Tablero de producción</li>
    <li><span class="tn">16</span> Métricas (KPIs del negocio)</li>
    <li><span class="tn">17</span> ¿Cuándo se descuenta el material?</li>
    <li><span class="tn">18</span> Flujo de trabajo recomendado</li>
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
    <tr><td><b>Impresión terminada</b></td><td>Resta el material gastado cuando un trabajo se marca como "Impreso".</td></tr>
</table>
<div class="box">
    <b>Importante:</b> aprobar un presupuesto <b>no</b> descuenta material. El descuento ocurre
    más adelante, cuando cada pieza se imprime. El porqué de esto está explicado en el punto 17.
</div>
<div class="ej">
    <span class="tag">EJEMPLO</span> Comprás 5 rollos de PLA Negro de 1 kg y confirmás la compra:
    el stock de PLA Negro sube <b>+5.000 g</b> y queda un movimiento "Compra +5.000 g". Después
    imprimís un pedido que usa 250 g y marcás el trabajo como "Impreso": el stock baja
    <b>−250 g</b> con un movimiento "Producción". En ningún momento tocaste el número de stock a mano.
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
    <tr><td>Producción (impresión)</td><td>Una pieza se marcó como "Impreso" y se descontó su material (resta).</td></tr>
    <tr><td>Ajuste manual</td><td>Corregiste el stock a mano (suma o resta).</td></tr>
</table>
<p>Esta vista es de lectura: no se edita acá, sirve para auditar y entender por qué cambió el stock.</p>
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Una semana cualquiera en el historial de PLA Negro:</div>
<table>
    <tr><th>Fecha</th><th>Material</th><th>Cantidad</th><th>Motivo</th></tr>
    <tr><td>02/06</td><td>PLA Negro</td><td>+3.000 g</td><td>Compra</td></tr>
    <tr><td>04/06</td><td>PLA Negro</td><td>−250 g</td><td>Producción</td></tr>
    <tr><td>05/06</td><td>PLA Negro</td><td>−120 g</td><td>Producción</td></tr>
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
    Otro caso: se te cayó y rompió medio rollo, cargás <b>−400</b> con nota "rollo dañado". Si el
    stock decía 300 g y restás 400, queda en <b>0</b> (nunca en negativo).
</div>
<div class="box">
    <b>Tip:</b> si querés dejar el stock en un valor exacto, fijate cuánto hay hoy y cargá la
    diferencia. El stock nunca queda en negativo: si restás de más, queda en 0.
</div>

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
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Costeo de un <b>Llavero con logo</b>:</div>
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
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Presupuesto para el cliente <b>"Estudio Belgrano"</b>:</div>
<table>
    <tr><th>Producto</th><th>Cantidad</th><th>Precio unit.</th><th>Subtotal</th></tr>
    <tr><td>Llavero con logo</td><td>2</td><td>$1.000</td><td>$2.000</td></tr>
    <tr><td>Maceta hexagonal</td><td>1</td><td>$4.500</td><td>$4.500</td></tr>
    <tr><td colspan="3"><b>Subtotal productos</b></td><td><b>$6.500</b></td></tr>
    <tr><td colspan="3">Costo fijo (envío)</td><td>$1.200</td></tr>
    <tr><td colspan="3"><b>Total (redondeo a $100)</b></td><td><b>$7.700</b></td></tr>
</table>
<p>Son 3 piezas en total. Ese precio unitario queda <b>congelado</b> en el presupuesto: aunque
   después cambies el costeo del producto, este presupuesto mantiene los $1.000 y $4.500.</p>
<h3>Estados del presupuesto</h3>
<p>Borrador → Enviado → Aprobado → En producción → Completado (o Cancelado). El estado te ayuda
   a seguir en qué etapa está cada pedido. Cada cambio de estado importante queda registrado
   con su fecha (enviado, aprobado, inicio y fin de producción, completado).</p>
<div class="box">
    <b>El estado se sincroniza solo con la producción:</b> cuando una pieza del pedido empieza a
    imprimirse, el presupuesto pasa a "En producción"; cuando se terminan todas, pasa a
    "Completado". No tenés que cambiarlo a mano (aunque podés).
</div>
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
<div class="ej"><span class="tag">EJEMPLO</span> &nbsp;Tu parque de máquinas:</div>
<table>
    <tr><th>Máquina</th><th>Activa</th><th>Multicolor (AMS)</th><th>En cola</th></tr>
    <tr><td>Bambu Lab A1 Combo #1</td><td>Sí</td><td>Sí</td><td>4</td></tr>
    <tr><td>Bambu Lab A1 Combo #2</td><td>Sí</td><td>Sí</td><td>3</td></tr>
    <tr><td>Ender 3 V3 Plus</td><td>Sí</td><td>No</td><td>2</td></tr>
</table>
<p>Un llavero de un solo color puede ir a cualquiera de las tres; una pieza marcada multicolor
   solo puede ir a una de las dos Bambu.</p>
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
<div class="ej">
    <span class="tag">EJEMPLO</span> El presupuesto de "Estudio Belgrano" (2 llaveros + 1 maceta)
    genera al aprobarse 2 trabajos: "Llavero ×2" a la Bambu #1 (posición 4) y "Maceta ×1" a la
    Ender (posición 2). Cuando arrancás el llavero, lo marcás <b>Imprimiendo</b>; al sacarlo de la
    cama lo marcás <b>Impreso</b> y ahí recién se descuentan 24 g de filamento (12 g × 2) y 2 argollas.
</div>
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
<div class="ej">
    <span class="tag">EJEMPLO</span> La Bambu #1 muestra: "Llavero ×2 — arranca hoy 14:00, termina
    14:48", "Soporte ×5 — arranca 14:48, termina 17:20", y abajo "Máquina libre a partir de las
    17:20". Así sabés que si entra un pedido nuevo urgente a esa máquina, antes de las 17:20 no
    arranca.
</div>

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
<div class="ej">
    <span class="tag">EJEMPLO</span> Entrás un lunes a la mañana y el tablero te dice: Bambu #1
    imprimiendo "Maceta ×3" (termina 11:20); próxima entrega "Estudio Belgrano" para el 27/06; y en
    <b>Comprar materia prima</b>: "PLA Negro: faltan 800 g, se agota el 26/06". Sabés que tenés que
    comprar PLA Negro antes del martes.
</div>
<div class="ok">
    <b>Para qué sirve:</b> entrás a la mañana, mirás el tablero y sabés qué está corriendo, cuándo
    entregás y qué tenés que ir a comprar antes de quedarte sin material.
</div>

<!-- METRICAS -->
<h2>16. Métricas (KPIs del negocio)</h2>
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
    <tr><td class="cl">Engranaje a pedido — 25</td><td><div style="background:#db2777; width:60pt; height:11px;">&nbsp;</div></td></tr>
    <tr><td class="cl">Posavasos — 12</td><td><div style="background:#65a30d; width:29pt; height:11px;">&nbsp;</div></td></tr>
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
    <tr><td>Conversión</td><td>De los presupuestos enviados en el período, cuántos se aprobaron.</td><td>66,7% (8 de 12)</td></tr>
    <tr><td>Margen bruto</td><td>(Facturación − costo de lo vendido) ÷ facturación.</td><td>50%</td></tr>
    <tr><td>Tiempo de ciclo</td><td>Días promedio entre aprobar y entregar (completar).</td><td>6,5 días</td></tr>
</table>
<p>Además vas a ver tres tablas: <b>productos más vendidos</b> (por cantidad y por $), <b>top
   clientes</b> (por facturación) y el <b>embudo de estados</b>, que es una foto de cuántos
   presupuestos hay <i>hoy</i> en cada estado (no del período).</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> Embudo de estados (foto actual): 3 Borrador · 2 Enviado ·
    1 Aprobado · 4 En producción · 10 Completado · 1 Cancelado. Te muestra de un vistazo cuántos
    pedidos tenés "trabados" en cada etapa.
</div>

<h3>B) Producción (verde)</h3>
<table>
    <tr><th>Indicador</th><th>Qué mide</th><th>Ejemplo (mes)</th></tr>
    <tr><td>Piezas impresas</td><td>Suma de piezas de los trabajos terminados en el período.</td><td>320</td></tr>
    <tr><td>Horas impresas</td><td>Horas de impresión de esos trabajos terminados.</td><td>142,5 h</td></tr>
    <tr><td>Tasa de reimpresión</td><td>Reimpresiones por falla ÷ trabajos impresos.</td><td>4,7% (3 de 64)</td></tr>
    <tr><td>Cumplimiento de entrega</td><td>Entregados a tiempo ÷ entregados con fecha pactada.</td><td>87,5% (7 de 8)</td></tr>
</table>
<p>Y la tabla <b>Uso por máquina</b>, con trabajos, piezas y horas por impresora.</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> Uso por máquina del mes: Bambu #1 → 28 trabajos / 180 piezas /
    78 h; Bambu #2 → 22 / 110 / 51 h; Ender → 9 / 30 / 13,5 h. Ves cuál está más cargada.
</div>

<h3>C) Inventario y costos (ámbar)</h3>
<table>
    <tr><th>Indicador</th><th>Qué mide</th><th>Ejemplo (mes)</th></tr>
    <tr><td>Gasto en compras</td><td>Total de las compras confirmadas en el período.</td><td>$380.000 (3 compras)</td></tr>
    <tr><td>Consumo de material ($)</td><td>Valor del material descontado al imprimir.</td><td>$210.000</td></tr>
    <tr><td>Filamento consumido</td><td>Gramos de filamento descontados por impresión.</td><td>11.800 g</td></tr>
    <tr><td>Insumos bajo stock mínimo</td><td>Cuántos materiales están por debajo del mínimo hoy.</td><td>2</td></tr>
</table>
<div class="box">
    <b>Qué fecha define cada métrica:</b> las ventas se miden por <b>fecha de aprobación</b>, la
    producción por <b>fin de impresión</b> y las compras por <b>fecha de confirmación</b>. El embudo
    de estados y los insumos bajo stock son una <b>foto del momento actual</b>, no dependen del
    período elegido.
</div>

<!-- DESCUENTO -->
<h2>17. ¿Cuándo se descuenta el material?</h2>
<p>El material se descuenta del inventario <b>cuando cada pieza se imprime</b> (cuando marcás el
   trabajo como "Impreso"), no cuando aprobás el presupuesto.</p>
<h3>Por qué es así</h3>
<p>Porque es lo que pasa de verdad: el filamento se gasta al imprimir, no al aceptar el pedido.
   Y porque hace que el aviso de "comprar materia prima" del tablero sea correcto. Si el stock se
   restara al aprobar, el tablero contaría el mismo material dos veces (una al aprobar y otra al
   proyectar la cola) y te diría que falta más de lo que falta en realidad.</p>
<div class="ej">
    <span class="tag">EJEMPLO</span> Aprobás un pedido de 10 macetas (300 g c/u = 3.000 g). Tu stock
    de PLA sigue mostrando lo mismo: aprobar <b>no</b> descontó nada, solo te avisó "ojo, vas a
    necesitar 3.000 g". Recién cuando imprimís y marcás cada maceta como "Impreso", el stock va
    bajando de a 300 g por maceta.
</div>
<div class="box">
    <b>En resumen:</b> aprobar = arma la cola y te <i>avisa</i> si el stock no alcanza.
    Imprimir (marcar "Impreso") = descuenta el material de verdad y queda registrado como
    movimiento de "Producción".
</div>

<!-- FLUJO -->
<h2>18. Flujo de trabajo recomendado</h2>
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
    <li><span class="step">10.</span> Una vez por semana o por mes, mirá <b>Métricas</b> para ver cómo viene el negocio y bajá el Excel si querés guardarlo.</li>
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
