"""Helpers de formato compartidos por los admin de budgets/production.

Nada de lógica de negocio acá: solo texto para mostrar en el admin.
"""


def format_hours(value):
    """Convierte horas decimales (ej. 3.67) a un texto fácil de leer en
    horas y minutos (ej. "3h 40m"), en vez del decimal crudo que confunde
    (0.67 h NO son 67 minutos, son 40).
    """
    if value is None:
        return "-"
    total_minutes = round(float(value) * 60)
    if total_minutes < 0:
        total_minutes = 0
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
