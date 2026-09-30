/*
 * Ficha de "Trabajo de producción": si el trabajo ya estaba Imprimiendo al
 * abrir la página y se cambia la máquina, pide una confirmación extra antes
 * de guardar. El servidor igual permite el cambio (es la vía "sancionada"
 * para reasignar un trabajo que está imprimiendo, a diferencia del listado
 * donde se bloquea de un solo click) — esto es solo para que no sea un
 * cambio "silencioso": el usuario ve el aviso ANTES de guardar, no solo
 * después en el mensaje de confirmación.
 */
document.addEventListener("DOMContentLoaded", function () {
    var machineSelect = document.getElementById("id_machine");
    var statusSelect = document.getElementById("id_status");
    if (!machineSelect || !statusSelect) {
        return;
    }
    var form = machineSelect.closest("form");
    if (!form) {
        return;
    }

    var initialMachine = machineSelect.value;
    var initialStatus = statusSelect.value;

    form.addEventListener("submit", function (event) {
        if (
            initialStatus === "PRINTING" &&
            machineSelect.value !== initialMachine
        ) {
            // El mensaje ya viene traducido al idioma activo desde el
            // servidor (ver ProductionJobAdmin.formfield_for_foreignkey),
            // porque este archivo estático no puede usar {% translate %}.
            var msg =
                machineSelect.getAttribute("data-confirm-msg") ||
                "Este trabajo está Imprimiendo. ¿Confirmás cambiarlo de " +
                    "máquina? Las corridas que ya se hicieron van a quedar " +
                    "a nombre de la máquina anterior.";
            if (!window.confirm(msg)) {
                event.preventDefault();
            }
        }
    });
});
