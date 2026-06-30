from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class GastosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gastos"
    verbose_name = _("Gastos")
