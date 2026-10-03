from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CajaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "caja"
    verbose_name = _("Caja")
