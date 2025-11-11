{ "type": "clearinghouseState", "user": "0x..." }
```  [oai_citation:0‡Chainstack](https://docs.chainstack.com/reference/hyperliquid-info-clearinghousestate?utm_source=chatgpt.com)  

Te dejo a continuación **`hyper_alerts.py` COMPLETO y CORREGIDO**:

- Usa `type: "clearinghouseState"` en todas las llamadas de wallet.
- Extrae bien el `coin` desde `position.coin`.
- Mantiene `/start`, `/wallet` y el nuevo `/walletdebug`.

Copia/pega TODO el archivo en GitHub y reemplaza el actual:

```python
#!/usr/bin/env python3
import os
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from flask import request

# ============ CONFIG (por variables de entorno) ============
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()  # grupo para alertas
HL_TRADER_ADDRESS   = os.getenv("HL_TRADER_ADDRESS", "").strip()

POLL_SECONDS        = int(os.getenv("POLL_SECONDS", "30"))       # frecuencia de consulta HL
STATE_FILE          = "state.json"
HL_INFO_URL         = "https://api.hyperliquid.xyz/info"

# Offset horario (ej. -6 para CDMX si la API está en UTC)
TIME_OFFSET_HOURS   = int(os.getenv("TIME_OFFSET_HOURS", "0"))

# Control de envíos a Telegram
MIN_SECONDS_BETWEEN_MSGS = 1.2   # throttle (~1 msg/seg)
MAX_MSGS_PER_CYCLE       = 5     # si hay más nuevos en un ciclo, se manda resumen
MAX_SEEN_IDS             = 500   # anti-duplicados
# ===========================================================
_last_send_ts = 0.0


# ===================== UTILIDADES DE TIEMPO =====================
def ts_to_local_str(ts, with_seconds: bool = False) -> str:
    """
    Convierte un timestamp (segundos o milisegundos desde epoch)
    a string ajustado por TIME_OFFSET_HOURS.
    """
    t = float(ts)
    if t > 10_000_000_000:  # milisegundos
        t = t / 1000.0
    dt = datetime.fromtimestamp(t, tz=timezone.utc) + timedelta(hours=TIME_OFFSET_HOURS)
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    return dt.strftime(fmt)


def fmt_time(ts) -> str:
    # usamos la misma función pero con segundos
    return ts_to_local_str(ts, with_seconds=True)


# ===================== ESTADO LOCAL =====================
def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "last_ts": 0,
        "seen_ids": [],
        "sent_raw_once": False,
    }


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print("⚠️ No pude guardar estado:", e)


# ===================== TELEGRAM (ENVÍO) =====================
def _sleep_until_next_slot() -> None:
    global _last_send_ts
    now = time.time()
    elapsed = now - _last_send_ts
    if elapsed < MIN_SECONDS_BETWEEN_MSGS:
        time.sleep(MIN_SECONDS_BETWEEN_MSGS - elapsed)
    _last_send_ts = time.time()


def send_telegram(text: str, chat_id: int | str | None = None, max_retries: int = 3) -> None:
    if not TELEGRAM_TOKEN:
        print("❌ Falta TELEGRAM_TOKEN.")
        return
    if chat_id is None:
        if not TELEGRAM_CHAT_ID:
            print("❌ Falta TELEGRAM_CHAT_ID y no se especificó chat_id.")
            return
        chat_id = TELEGRAM_CHAT_ID

    _sleep_until_next_slot()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    attempt = 0
    while attempt <= max_retries:
        try:
            r = requests.post(url, data=data, timeout=20)
            if r.status_code == 429:
                try:
                    j = r.json()
                    retry_after = j.get("parameters", {}).get("retry_after") or j.get("retry_after") or 3
                except Exception:
                    retry_after = 3
                print(f"⏳ Rate limit (429). Esperando {retry_after}s …")
                time.sleep(int(retry_after) + 1)
                attempt += 1
                continue
            if not r.ok:
                print("⚠️ Error Telegram:", r.status_code, r.text[:200])
            return
        except Exception as e:
            print("❌ Error conectando con Telegram:", e)
            time.sleep(2)
            attempt += 1


# ===================== HYPERLIQUID: FETCH =====================
def _post_json(url: str, payload: dict) -> requests.Response:
    return requests.post(url, json=payload, timeout=30, headers={"Content-Type": "application/json"})


def _normalize_fill(fill: dict) -> dict:
    coin = fill.get("coin") or fill.get("symbol") or fill.get("asset") or "?"
    side = (fill.get("side") or fill.get("dir") or "?").upper()
    px   = fill.get("px") or fill.get("price") or fill.get("p") or "?"
    sz   = fill.get("sz") or fill.get("size")  or fill.get("q") or "?"
    tid  = fill.get("tid") or fill.get("tradeId") or fill.get("id")
    ts   = fill.get("timestamp") or fill.get("ts") or fill.get("time") or 0
    return {"coin": coin, "side": side, "px": px, "sz": sz, "tid": tid, "ts": ts, "_raw": fill}


def fetch_fills_resilient(address: str, since_ts: int | None) -> list[dict]:
    trials = [
        ("POST userFills",      lambda: _post_json(HL_INFO_URL, {"type": "userFills", "user": address})),
        ("POST fills",          lambda: _post_json(HL_INFO_URL, {"type": "fills", "user": address})),
        ("POST fills+n",        lambda: _post_json(HL_INFO_URL, {"type": "fills", "user": address, "n": 500})),
    ]
    if since_ts and since_ts > 0:
        trials.append(("POST fills+startTime", lambda: _post_json(
            HL_INFO_URL, {"type": "fills", "user": address, "startTime": int(since_ts)}
        )))
    trials.append(("GET userFills", lambda: requests.get(
        f"{HL_INFO_URL}?type=userFills&user={address}", timeout=30
    )))

    errors = []
    for label, fn in trials:
        try:
            r = fn()
            if r.ok:
                data = r.json()
                if isinstance(data, list):
                    raw = data
                elif isinstance(data, dict):
                    raw = next((v for v in data.values() if isinstance(v, list)), None)
                    if raw is None:
                        errors.append(f"{label}: dict sin lista usable")
                        continue
                else:
                    errors.append(f"{label}: tipo no soportado: {type(data)}")
                    continue

                fills = [_normalize_fill(x) for x in raw]
                if since_ts and since_ts > 0:
                    fills = [f for f in fills if (f["ts"] and f["ts"] > since_ts)]
                return fills
            else:
                errors.append(f"{label}: HTTP {r.status_code} -> {r.text[:120]}")
        except Exception as e:
            errors.append(f"{label}: EXC {e}")
    if errors:
        print("❌ fetch_fills_resilient falló:", " | ".join(errors))
    return []


def fetch_wallet_state_resilient(address: str) -> dict:
    """
    Usa el tipo 'clearinghouseState' de Hyperliquid.
    Devuelve:
      {
        "equity": float|None,
        "withdrawable": float|None,
        "positions": [
          {"coin","szi","entry","liq","roe","pos_value"}
        ]
      }
    """
    errors = []
    try:
        # 👇 tipo correcto según docs: clearinghouseState
        r = _post_json(HL_INFO_URL, {"type": "clearinghouseState", "user": address})
        if not r.ok:
            errors.append(f"wallet: HTTP {r.status_code} -> {r.text[:200]}")
        else:
            data = r.json()
            if isinstance(data, list) and data:
                d = data[0]
            elif isinstance(data, dict):
                d = data
            else:
                errors.append(f"wallet: resp no dict/list -> {type(data)}")
                d = {}

            margin = d.get("marginSummary") or d.get("crossMarginSummary") or {}
            equity = margin.get("accountValue") or margin.get("accountValueTotal")
            withdrawable = d.get("withdrawable")

            raw_pos = d.get("assetPositions") or []
            positions = []

            for ap in raw_pos:
                # estructura docs: {"position": {...}, "type": "..."}
                core = ap.get("position") or ap.get("perpPosition") or ap
                coin = core.get("coin") or ap.get("coin") or ap.get("asset") or ap.get("symbol") or "?"

                # tamaño
                szi_raw = (
                    core.get("szi")
                    or core.get("size")
                    or core.get("positionSize")
                    or core.get("sz")
                    or 0
                )
                try:
                    szi = float(szi_raw)
                except Exception:
                    szi = szi_raw  # si no se puede convertir, lo dejamos tal cual

                # valor de posición (USD)
                pos_val_raw = (
                    core.get("positionValue")
                    or ap.get("positionValue")
                    or core.get("posValue")
                    or 0
                )
                try:
                    pos_value = float(pos_val_raw)
                except Exception:
                    pos_value = 0.0

                entry = core.get("entryPx") or core.get("entry") or core.get("entryPrice")
                liq   = core.get("liqPx") or core.get("liquidationPx") or core.get("liq")
                roe_raw = core.get("returnOnEquity") or core.get("roe") or core.get("ROE")
                try:
                    roe = float(roe_raw) if roe_raw is not None else None
                except Exception:
                    roe = None

                positions.append({
                    "coin": coin,
                    "szi": szi,
                    "entry": entry,
                    "liq": liq,
                    "roe": roe,
                    "pos_value": pos_value,
                })

            return {
                "equity": equity,
                "withdrawable": withdrawable,
                "positions": positions,
            }

    except Exception as e:
        errors.append(f"wallet: EXC {e}")

    if errors:
        print("❌ fetch_wallet_state_resilient falló:", " | ".join(errors))
    return {"equity": None, "withdrawable": None, "positions": []}


# ===================== FORMATEADORES =====================
def build_fill_message(addr: str, f: dict) -> str:
    lines = ["⚡ Actividad detectada", f"Trader: `{addr}`"]
    if f.get("ts"):
        lines.append(f"Hora: {fmt_time(f['ts'])}")
    lines += [
        f"Par: {f.get('coin','?')}",
        f"Lado: {f.get('side','?')}",
        f"Precio: {f.get('px','?')}",
        f"Tamaño: {f.get('sz','?')}",
    ]
    if f.get("tid") is not None:
        lines.append(f"tradeId: {f['tid']}")
    return "\n".join(lines)


def build_fills_summary(addr: str, fills: list[dict]) -> str:
    fills_sorted = sorted(fills, key=lambda x: x.get("ts", 0) or 0)
    lines = [f"📬 {len(fills_sorted)} eventos nuevos del trader `{addr}`"]
    for f in fills_sorted[:5]:
        ts = fmt_time(f.get("ts", 0) or 0)
        lines.append(f"- [{ts}] {f.get('coin','?')} {f.get('side','?')} sz={f.get('sz','?')} px={f.get('px','?')}")
    if len(fills_sorted) > 5:
        lines.append(f"… y {len(fills_sorted) - 5} más.")
    return "\n".join(lines)


def build_wallet_snapshot(addr: str, wallet: dict, fills24_top5: list[dict]) -> str:
    lines = [f"🔎 Wallet: `{addr}`"]
    eq = wallet.get("equity")
    wd = wallet.get("withdrawable")
    if eq is not None:
        lines.append(f"Equity: {eq}")
    if wd is not None:
        lines.append(f"Withdrawable: {wd}")

    pos = []
    for p in wallet.get("positions", []):
        szi = p.get("szi")
        pos_value = p.get("pos_value", 0) or 0
        # activa si tamaño != 0 o valor > 0
        if szi in (0, 0.0, None, "0", "0.0", "") and pos_value == 0:
            continue
        pos.append(p)

    if pos:
        lines.append("Posiciones activas:")
        for p in pos[:10]:
            lines.append(
                f"• {p.get('coin','?')}: "
                f"szi={p.get('szi')} "
                f"posValue={p.get('pos_value', 0)} "
                f"entry={p.get('entry')} "
                f"liq={p.get('liq')} "
                f"ROE={p.get('roe')}"
            )
        if len(pos) > 10:
            lines.append(f"… y {len(pos)-10} más.")
    else:
        lines.append("Sin posiciones activas.")

    if fills24_top5:
        lines.append("Fills 24h (top 5):")
        for f in fills24_top5:
            ts = ts_to_local_str(f.get("ts", 0) or 0, with_seconds=False)
            emoji = "🟢" if (f["side"].startswith("B") or f["side"] == "BUY") else "🔴"
            lines.append(f"• {emoji} {f['coin']} {f['sz']}@{f['px']} {ts}")
    return "\n".join(lines)


# ===================== SNAPSHOT REUTILIZABLE =====================
def send_wallet_snapshot(chat_id: int | str | None = None) -> None:
    wallet = fetch_wallet_state_resilient(HL_TRADER_ADDRESS)
    now = datetime.now(timezone.utc)
    since_24h = int((now - timedelta(hours=24)).timestamp() * 1000)
    fills24 = fetch_fills_resilient(HL_TRADER_ADDRESS, since_24h)
    fills24.sort(key=lambda f: f.get("ts", 0) or 0, reverse=True)
    top5 = fills24[:5]
    msg_snap = build_wallet_snapshot(HL_TRADER_ADDRESS, wallet, top5)
    send_telegram(msg_snap, chat_id=chat_id)


def send_wallet_debug(chat_id: int | str | None = None) -> None:
    """
    Comando de depuración: envía el JSON crudo del wallet (o al menos assetPositions)
    para ver exactamente qué campos devuelve Hyperliquid.
    """
    try:
        r = _post_json(HL_INFO_URL, {"type": "clearinghouseState", "user": HL_TRADER_ADDRESS})
        if not r.ok:
            send_telegram(f"walletdebug HTTP {r.status_code}: {r.text[:200]}", chat_id=chat_id)
            return

        data = r.json()
        # Nos interesa sobre todo assetPositions
        if isinstance(data, dict):
            payload = {
                "marginSummary": data.get("marginSummary"),
                "withdrawable": data.get("withdrawable"),
                "assetPositions": data.get("assetPositions"),
            }
        elif isinstance(data, list) and data:
            d = data[0]
            payload = {
                "marginSummary": d.get("marginSummary"),
                "withdrawable": d.get("withdrawable"),
                "assetPositions": d.get("assetPositions"),
            }
        else:
            payload = {"raw": data}

        text = json.dumps(payload, indent=2)
        # Telegram tiene límite ~4096 chars, recortamos por si acaso
        if len(text) > 3500:
            text = text[:3500] + "\n...(truncado)..."

        send_telegram("📦 walletdebug:\n" + text, chat_id=chat_id)
    except Exception as e:
        send_telegram(f"walletdebug error: {e}", chat_id=chat_id)


# ===================== TELEGRAM WEBHOOK =====================
def handle_telegram_update(update: dict) -> None:
    """
    Procesa un update recibido vía webhook de Telegram.
    Soporta:
      /start
      /wallet
      /walletdebug
    """
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    if not text.startswith("/"):
        return

    text_lower = text.lower()
    if text_lower.startswith("/start"):
        send_telegram(
            f"👋 HyperWhaleBot activo.\n"
            f"Monitoreando: `{HL_TRADER_ADDRESS}`\n"
            f"Intervalo: {POLL_SECONDS}s\n\n"
            f"Comandos:\n"
            f"• /wallet – snapshot manual de la cartera\n"
            f"• /walletdebug – info cruda de la cartera (debug)\n",
            chat_id=chat_id,
        )
    elif text_lower.startswith("/walletdebug"):
        send_wallet_debug(chat_id=chat_id)
    elif text_lower.startswith("/wallet"):
        send_wallet_snapshot(chat_id=chat_id)


# ===================== LOOP PRINCIPAL (ALERTAS) =====================
def run_bot() -> None:
    if not TELEGRAM_TOKEN or not HL_TRADER_ADDRESS:
        print("❌ Faltan TELEGRAM_TOKEN o HL_TRADER_ADDRESS.")
        return

    print("✅ Iniciando bot HyperWhaleBot (rate-safe + comandos por webhook)…")
    if TELEGRAM_CHAT_ID:
        send_telegram("👋 Bot iniciado. Monitoreo activo del trader en Hyperliquid.")

    state = load_state()
    last_ts = int(state.get("last_ts", 0))
    seen_ids = set(state.get("seen_ids", []))
    sent_raw_once = state.get("sent_raw_once", False)

    while True:
        try:
            fills = fetch_fills_resilient(HL_TRADER_ADDRESS, last_ts)
            new_items: list[dict] = []
            if fills:
                fills.sort(key=lambda f: f.get("ts", 0) or 0)
                for f in fills:
                    tid = f.get("tid")
                    ts  = f.get("ts", 0) or 0
                    is_new_by_ts = (ts and ts > last_ts)
                    is_new_by_id = (tid is not None and tid not in seen_ids)

                    if not ts and tid is None:
                        if not sent_raw_once:
                            new_items.append(f)
                            sent_raw_once = True
                        continue

                    if is_new_by_ts or is_new_by_id:
                        new_items.append(f)
                        if ts > last_ts:
                            last_ts = ts
                        if tid is not None:
                            seen_ids.add(tid)

            if new_items and TELEGRAM_CHAT_ID:
                if len(new_items) > MAX_MSGS_PER_CYCLE:
                    send_telegram(build_fills_summary(HL_TRADER_ADDRESS, new_items))
                else:
                    for f in new_items:
                        send_telegram(build_fill_message(HL_TRADER_ADDRESS, f))

            state["last_ts"] = int(last_ts)
            state["seen_ids"] = list(seen_ids)[-MAX_SEEN_IDS:]
            state["sent_raw_once"] = sent_raw_once
            save_state(state)

        except Exception as e:
            print("❌ Error en loop principal:", e)

        time.sleep(POLL_SECONDS)


# ===================== HOOKS HTTP (Flask) =====================
def register_http_hooks() -> None:
    try:
        from keep_alive import app
    except Exception as e:
        print("⚠️ No pude registrar hooks HTTP extra:", e)
        return

    @app.post("/telegram-webhook")
    def telegram_webhook():
        try:
            update = request.get_json(force=True, silent=True) or {}
            handle_telegram_update(update)
            return {"ok": True}, 200
        except Exception as e:
            print("❌ Error en telegram_webhook:", e)
            return {"ok": False, "error": str(e)}, 500

    @app.get("/snapshot")
    def snapshot():
        try:
            send_wallet_snapshot()
            return {"ok": True}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    @app.get("/ping")
    def ping():
        return {"pong": True}, 200
