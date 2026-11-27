"""Cancela los presupuestos Enviados que pasaron 10 días sin respuesta del cliente y avisa (27/11/2026).

Pensado para correr una vez por día (lo va a disparar Roque o una tarea programada de Railway):

    python manage.py vencer_presupuestos            # cancela y avisa
    python manage.py vencer_presupuestos --simular  # solo lista, no toca nada
"""

from django.core.management.base import BaseCommand

from budgets.models import Presupuesto


class Command(BaseCommand):
    help = "Cancela los presupuestos Enviados sin respuesta hace más de 10 días y avisa."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=Presupuesto.DIAS_VENCIMIENTO)
        parser.add_argument("--simular", action="store_true", help="Solo lista, no cancela.")

    def handle(self, *args, dias, simular, **opts):
        vencidos = Presupuesto.vencer_sin_respuesta(dias=dias, simular=simular)
        verbo = "Vencerían" if simular else "Cancelados por vencimiento"
        self.stdout.write(f"{verbo}: {len(vencidos)}")
        for v in vencidos:
            self.stdout.write(f"  #{v['id']} {v['cliente']} (vendió {v['vendedor'] or '—'}, enviado {v['enviado']})")
