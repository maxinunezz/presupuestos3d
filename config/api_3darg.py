"""Sincronización de costeo → 3darg-backend para productos de ecommerce.

Cuando un `Producto` de costeo representa un presupuesto compartido por
muchos diseños del catálogo (ver `Producto.es_producto_ecommerce`/
`ecommerce_template_name` en budgets/models.py — ej: "Cortante 4cm", "Shaker
grande"), este módulo le avisa a 3darg-backend para que cree o actualice el
`CostTemplate` correspondiente (identificado por una referencia estable, no
por el nombre — ver `external_ref` más abajo) con el precio y las medidas de
acá, y republique en Mercado Libre los productos de catálogo que ya estén
vinculados a ese template (vínculo que se arma a mano del otro lado, en el
admin de 3darg-backend).

Mismo criterio "vacío = apagado, nunca rompe el guardado" que
`config/zapier.py` y `config/slack.py`: sin `API_3DARG_URL`/`API_3DARG_TOKEN`
configurados, o ante cualquier error de red/API, no hace nada más que
loguearlo — guardar el `Producto` en el admin nunca falla por esto.
"""

import json
import logging
import urllib.request
from urllib.error import HTTPError, URLError

from django.conf import settings

logger = logging.getLogger(__name__)

COSTEO_SYNC_PATH = "/api/products/costeo-sync/"
TIMEOUT = 10


def sync_costeo_ecommerce(producto) -> dict | None:
    """POSTea a 3darg-backend el precio/medidas del template de costeo de
    `producto` para que cree/actualice el `CostTemplate` correspondiente y
    propague el precio a todas las variantes de catálogo ya vinculadas a
    ese template (y las vuelva a publicar en Mercado Libre si corresponde).

    `external_ref` es la clave real de upsert del lado de 3darg-backend —
    estable en base al `pk` de este `Producto`, así que renombrar el
    template (`ecommerce_template_name`) más adelante no rompe el link ya
    hecho ni duplica el template allá.

    Devuelve el dict de respuesta de 3darg-backend (`{"actualizados":...,
    "ml_republicados":..., "ml_errores":[...]}`), o `{"error": "..."}` si
    falló, o `None` si la integración está apagada. Nunca lanza excepción.
    """
    api_url = getattr(settings, "API_3DARG_URL", "").rstrip("/")
    token = getattr(settings, "API_3DARG_TOKEN", "")
    if not api_url or not token:
        logger.debug(
            "API_3DARG no configurado — no se sincroniza costeo de '%s' (sku=%s)",
            producto.name, producto.sku,
        )
        return None

    payload = {
        "external_ref": f"presupuestos3d:{producto.pk}",
        "nombre": producto.ecommerce_template_name,
        "sale_price": str(producto.sale_price),
    }
    for field in ("weight_kg", "length_cm", "width_cm", "height_cm"):
        value = getattr(producto, field)
        if value is not None:
            payload[field] = str(value)

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url}{COSTEO_SYNC_PATH}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Token {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error(
            "3darg-backend rechazó el sync de costeo (producto=%s, sku=%s): %s %s",
            producto.name, producto.sku, e.code, body_text,
        )
        return {"error": body_text}
    except URLError as e:
        logger.error(
            "Error de red al sincronizar costeo con 3darg-backend (producto=%s, sku=%s): %s",
            producto.name, producto.sku, e,
        )
        return {"error": str(e)}
    except (ValueError, KeyError) as e:
        logger.error(
            "Respuesta inesperada de 3darg-backend al sincronizar costeo (producto=%s): %s",
            producto.name, e,
        )
        return {"error": str(e)}
