"""Envío de avisos a Slack vía Incoming Webhook (gratis, sin pasar por Zapier).

Cada canal/aviso tiene su propia URL de Incoming Webhook, configurada por
variable de entorno (ver SLACK_WEBHOOKS en settings.py). Si un aviso no tiene
URL configurada, notificar() no hace nada.
"""

import json
import logging
import urllib.request
from urllib.error import URLError

from django.conf import settings

logger = logging.getLogger(__name__)


def notificar(aviso: str, texto: str) -> None:
    """Manda `texto` al Incoming Webhook de Slack asociado a `aviso`.

    Nunca lanza excepción: un webhook caído o mal configurado no debe romper
    el flujo de negocio (guardar un producto, aprobar un presupuesto, etc.).
    """
    url = settings.SLACK_WEBHOOKS.get(aviso)
    if not url:
        return

    body = json.dumps({"text": texto}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except URLError:
        logger.exception("Fallo al notificar a Slack (aviso=%s)", aviso)
