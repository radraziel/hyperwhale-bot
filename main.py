from keep_alive import keep_alive
from hyper_alerts import run_bot, register_http_hooks

if __name__ == "__main__":
    # registra endpoints HTTP (ej. /snapshot) ANTES de arrancar Flask
    register_http_hooks()
    # levanta el miniserver web para uptime robot y utilidades
    keep_alive()
    # arranca el loop del bot de alertas
    run_bot()
