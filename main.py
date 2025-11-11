from keep_alive import keep_alive
from hyper_alerts import run_bot, register_http_hooks

if __name__ == "__main__":
    # Registrar rutas HTTP (webhook, snapshot, etc.)
    register_http_hooks()
    # Levantar Flask en puerto 8080 (Render lo detecta solo)
    keep_alive()
    # Iniciar loop principal del bot (alertas)
    run_bot()
