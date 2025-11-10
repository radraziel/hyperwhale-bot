from keep_alive import keep_alive
from hyper_alerts import run_bot, register_http_hooks

if __name__ == "__main__":
    # Registrar endpoints HTTP (ej. /health ya está en keep_alive, /snapshot se puede agregar si quieres)
    register_http_hooks()
    # Levantar servidor Flask en puerto 8080 (Render lo detecta)
    keep_alive()
    # Iniciar loop principal del bot (alertas + lectura de comandos /start y /wallet)
    run_bot()
