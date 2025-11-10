#!/usr/bin/env python3
import os, time, json, requests
from datetime import datetime, timezone, timedelta

# ====== CONFIG (por env; en Render están en Environment) ======
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()
HL_TRADER_ADDRESS   = os.getenv("HL_TRADER_ADDRESS", "").strip()

POLL_SECONDS        = int(os.getenv("POLL_SECONDS", "30"))  # frecuencia de consulta
STATE_FILE          = "state.json"
HL_INFO_URL         = "https://api.hyperliquid.xyz/info"

# Control de envíos a Telegram
MIN_SECONDS_BETWEEN_MSGS = 1.2   # throttle (~1 msg/seg)
MAX_MSGS_PER_CYCLE       = 5     # si hay más, se manda resumen
MAX_SEEN_IDS             = 500
# =============================================================

_last_send_ts = 0.0

# ---- Utils Telegram ----
def _sleep_until_next_slot():
    global _last_send_ts
    now = time.time()
    elapsed = now - _last_send_ts
    if elapsed < MIN_SECONDS_BETWEEN_MSGS:
        time.sleep(MIN_SECONDS_BETWEEN_MSGS - elapsed)
    _last_send_ts = time.time()

def send_telegram(text: str, max_retries: int = 3):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
        return
    _sleep_until_next_slot()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
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

# ---- Estado local ----
def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"last_ts": 0, "seen_ids": [], "sent_raw_once": False}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print("⚠️ No pude guardar estado:", e)

def fmt_time(ts):
    t = int(ts)
    if t > 10_000_000_000:  # milisegundos
        t = t / 1000
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

# ---- Normalizadores / llamadas a API ----
def _normalize_fill(fill: dict):
    coin = fill.get("coin") or fill.get("symbol") or fill.get("asset") or "?"
    side = (fill.get("side") or fill.get("dir") or "?").upper()
    px   = fill.get("px") or fill.get("price") or fill.get("p") or "?"
    sz   = fill.get("sz") or fill.get("size")  or fill.get("q") or "?"
    tid  = fill.get("tid") or fill.get("tradeId") or fill.get("id")
    ts   = fill.get("timestamp") or fill.get("ts") or fill.get("time") or 0
    return {"coin": coin, "side": side, "px": px, "sz": sz, "tid": tid, "ts": ts, "_raw": fill}

def _post_json(url, payload):
    return requests.post(url, json=payload, timeout=30, headers={"Content-Type": "application/json"})

def fetch_fills_resilient(address: str, since_ts: int | None):
    trials = [
        ("POST userFills",      lambda: _post_json(HL_INFO_URL, {"type": "userFills", "user": address})),
        ("POST fills",          lambda: _post_json(HL_INFO_URL, {"type": "fills", "user": address})),
        ("POST fills+n",        lambda: _post_json(HL_INFO_URL, {"type": "fills", "user": address, "n": 500})),
    ]
    if since_ts and since_ts > 0:
        trials.append(("POST fills+startTime", lambda: _post_json(HL_INFO_URL, {"type": "fills", "user": address, "startTime": int(since_ts)})))
    trials.append(("GET userFills", lambda: requests.get(f"{HL_INFO_URL}?type=userFills&user={address}", timeout=30)))

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
                    errors.append(f"{label}: tipo no soportado: {type(data)}"); continue
                fills = [_normalize_fill(x) for x in raw]
                if since_ts and since_ts > 0:
                    fills = [f for f in fills if (f["ts"] and f["ts"] > since_ts)]
                return fills
            else:
                errors.append(f"{label}: HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"{label}: EXC {e}")
    if errors:
        print("❌ fetch_fills_resilient falló:", " | ".join(errors))
    return []

def fetch_wallet_state_resilient(address: str):
    """
    Devuelve un dict con 'equity', 'withdrawable', 'positions' (lista normalizada).
    Prueba varias variantes del endpoint de info.
    """
    trials = [
        ("POST userState", lambda: _post_json(HL_INFO_URL, {"type": "userState", "user": address})),
        ("POST wallet",    lambda: _post_json(HL_INFO_URL, {"type": "wallet", "user": address})),
        ("GET userState",  lambda: requests.get(f"{HL_INFO_URL}?type=userState&user={address}", timeout=30)),
    ]
    errors = []
    for label, fn in trials:
        try:
            r = fn()
            if not r.ok:
                errors.append(f"{label}: HTTP {r.status_code}")
                continue
            data = r.json()
            # Buscamos campos típicos
            # userState suele devolver: marginSummary / crossMarginSummary / withdrawable / assetPositions / time
            d = data if isinstance(data, dict) else {}
            equity = (d.get("marginSummary") or d.get("crossMarginSummary") or {}).get("accountValue")
            withdrawable = d.get("withdrawable")
            # posiciones
            raw_pos = d.get("assetPositions") or d.get("positions") or []
            positions = []
            for p in raw_pos:
                # tolerante a distintas formas:
                coin = p.get("coin") or p.get("asset") or p.get("symbol") or "?"
                # algunas respuestas meten el objeto bajo 'position' o 'perpPosition'
                core = p.get("position") or p.get("perpPosition") or p
                szi   = core.get("szi") or core.get("size") or 0
                entry = core.get("entryPx") or core.get("entry") or core.get("entryPrice")
                liq   = core.get("liqPx") or core.get("liquidationPx") or core.get("liq")
                roe   = core.get("roe") or core.get("ROE")
                if roe is None and entry and isinstance(entry, (int, float)) and isinstance(szi, (int, float)):
                    # sin mark/px actual no podemos calcular, así que omite
                    pass
                positions.append({"coin": coin, "szi": szi, "entry": entry, "liq": liq, "roe": roe})
            return {"equity": equity, "withdrawable": withdrawable, "positions": positions}
        except Exception as e:
            errors.append(f"{label}: EXC {e}")
    print("❌ fetch_wallet_state_resilient falló:", " | ".join(errors))
    return {"equity": None, "withdrawable": None, "positions": []}

# ---- Formateadores ----
def build_message(addr: str, f: dict):
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

def build_summary(addr: str, fills: list):
    fills_sorted = sorted(fills, key=lambda x: x.get("ts", 0) or 0)
    lines = [f"📬 {len(fills_sorted)} eventos nuevos del trader `{addr}`"]
    for f in fills_sorted[:5]:
        ts = fmt_time(f.get("ts", 0) or 0)
        lines.append(f"- [{ts}] {f.get('coin','?')} {f.get('side','?')} sz={f.get('sz','?')} px={f.get('px','?')}")
    if len(fills_sorted) > 5:
        lines.append(f"… y {len(fills_sorted) - 5} más.")
    return "\n".join(lines)

def build_wallet_snapshot(addr: str, wallet: dict, fills24_top5: list):
    lines = [f"🔎 Wallet: `{addr}`"]
    eq = wallet.get("equity"); wd = wallet.get("withdrawable")
    if eq is not None: lines.append(f"Equity: {eq}")
    if wd is not None: lines.append(f"Withdrawable: {wd}")
    # Posiciones activas (szi != 0)
    pos = [p for p in wallet.get("positions", []) if p.get("szi")]
    if pos:
        lines.append("Posiciones activas:")
        for p in pos[:10]:
            szi = p.get("szi"); entry = p.get("entry"); liq = p.get("liq"); roe = p.get("roe")
            lines.append(f"• {p.get('coin','?')}: szi={szi} entry={entry} liq={liq} ROE={roe}")
        if len(pos) > 10:
            lines.append(f"… y {len(pos)-10} más.")
    else:
        lines.append("Sin posiciones activas.")
    # Top fills 24h
    if fills24_top5:
        lines.append("Fills 24h (top 5):")
        for f in fills24_top5:
            ts = datetime.fromtimestamp((f['ts']/1000) if f['ts']>10_000_000_000 else f['ts'], tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")
            emoji = "🟢" if (f["side"].startswith("B") or f["side"]=="BUY") else "🔴"
            lines.append(f"• {emoji} {f['coin']} {f['sz']}@{f['px']} {ts}")
    return "\n".join(lines)

# ---- Bot loop (alertas) ----
def run_bot():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not HL_TRADER_ADDRESS:
        print("❌ Faltan TELEGRAM_TOKEN / TELEGRAM_CHAT_ID / HL_TRADER_ADDRESS.")
        return

    print("✅ Iniciando bot HyperWhaleBot (rate-safe)…")
    send_telegram("👋 Bot iniciado. Monitoreo activo del trader en Hyperliquid.")

    state = load_state()
    last_ts = int(state.get("last_ts", 0))
    seen_ids = set(state.get("seen_ids", []))
    sent_raw_once = state.get("sent_raw_once", False)

    while True:
        try:
            fills = fetch_fills_resilient(HL_TRADER_ADDRESS, last_ts)
            new_items = []
            if fills:
                fills.sort(key=lambda f: f.get("ts", 0) or 0)
                for f in fills:
                    tid = f.get("tid")
                    ts  = f.get("ts", 0) or 0
                    is_new_by_ts = (ts and ts > last_ts)
                    is_new_by_id = (tid is not None and tid not in seen_ids)

                    if not ts and tid is None:
                        if not sent_raw_once:
                            new_items.append(f); sent_raw_once = True
                        continue

                    if is_new_by_ts or is_new_by_id:
                        new_items.append(f)
                        if ts > last_ts: last_ts = ts
                        if tid is not None: seen_ids.add(tid)

            if new_items:
                if len(new_items) > MAX_MSGS_PER_CYCLE:
                    send_telegram(build_summary(HL_TRADER_ADDRESS, new_items))
                else:
                    for f in new_items:
                        send_telegram(build_message(HL_TRADER_ADDRESS, f))

                state["last_ts"] = int(last_ts)
                state["seen_ids"] = list(seen_ids)[-MAX_SEEN_IDS:]
                state["sent_raw_once"] = sent_raw_once
                save_state(state)

        except Exception as e:
            print("❌ Error en loop:", e)

        time.sleep(POLL_SECONDS)

# ---- HTTP hooks (para /snapshot) ----
def register_http_hooks():
    """
    Se llama desde main.py antes de levantar Flask, para registrar
    un handler HTTP /snapshot que dispara un resumen hacia Telegram.
    """
    try:
        # Importamos aquí para no crear dependencia circular
        from keep_alive import app
    except Exception as e:
        print("⚠️ No pude registrar hooks HTTP:", e)
        return

    @app.get("/snapshot")
    def snapshot():
        try:
            # 1) wallet (equity, withdrawable, posiciones)
            wallet = fetch_wallet_state_resilient(HL_TRADER_ADDRESS)

            # 2) top fills 24h
            now = datetime.now(timezone.utc)
            since_24h = int((now - timedelta(hours=24)).timestamp() * 1000)
            fills24 = fetch_fills_resilient(HL_TRADER_ADDRESS, since_24h)
            # ordenar desc por ts y tomar top 5 (por tamaño si quieres, aquí es por tiempo)
            fills24.sort(key=lambda f: f.get("ts", 0) or 0, reverse=True)
            top5 = fills24[:5]

            msg = build_wallet_snapshot(HL_TRADER_ADDRESS, wallet, top5)
            send_telegram(msg)
            return {"ok": True, "sent": True}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500
