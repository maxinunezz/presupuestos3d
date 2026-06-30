from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BudgetsConfig(AppConfig):
    name = 'budgets'
    verbose_name = _('Presupuestos')
