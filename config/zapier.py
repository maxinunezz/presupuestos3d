"""Envío de eventos de negocio a Zapier vía "Webhooks by Zapier" (Catch Hook).

Cada evento tiene su propia URL de Catch Hook, configurada por variable de
entorno (ver ZAPIER_WEBHOOKS en settings.py). Si un evento no tiene URL
configurada, notificar() no hace nada.
"""

import json
import logging
import urllib.request
from urllib.error import URLError

from django.conf import settings

logger = logging.getLogger(__name__)


def notificar(evento: str, payload: dict) -> None:
    """Dispara el webhook de Zapier asociado a `evento` con `payload` como JSON.

    Nunca lanza excepción: un webhook caído o mal configurado no debe romper
    el flujo de negocio (aprobar un presupuesto, descontar stock, etc.).
    """
    url = settings.ZAPIER_WEBHOOKS.get(evento)
    if not url:
        return

    body = json.dumps({"evento": evento, **payload}, default=str).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except URLError:
        logger.exception("Fallo al notificar a Zapier (evento=%s)", evento)
