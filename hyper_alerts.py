{\rtf1\ansi\ansicpg1252\cocoartf2865
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 #!/usr/bin/env python3\
import os\
import time\
import json\
import requests\
from datetime import datetime, timezone\
\
# ====== CONFIG (por env; en Replit usa Secrets) ======\
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()\
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()\
HL_TRADER_ADDRESS = os.getenv("HL_TRADER_ADDRESS", "").strip()\
\
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))  # frecuencia de consulta\
STATE_FILE = "state.json"                            # persistencia simple\
HL_INFO_URL = "https://api.hyperliquid.xyz/info"\
\
# Control de env\'edos a Telegram\
MIN_SECONDS_BETWEEN_MSGS = 1.2   # throttle por chat (~1 msg/seg)\
MAX_MSGS_PER_CYCLE = 5           # si hay m\'e1s, se env\'eda un resumen\
MAX_SEEN_IDS = 500               # memoria anti-duplicados\
# =====================================================\
\
_last_send_ts = 0.0\
\
def _sleep_until_next_slot():\
    """Respeta el throttle de env\'edo por chat."""\
    global _last_send_ts\
    now = time.time()\
    elapsed = now - _last_send_ts\
    if elapsed < MIN_SECONDS_BETWEEN_MSGS:\
        time.sleep(MIN_SECONDS_BETWEEN_MSGS - elapsed)\
    _last_send_ts = time.time()\
\
def send_telegram(text: str, max_retries: int = 3):\
    """\
    Env\'eda mensaje a Telegram respetando throttle y reintenta si hay 429.\
    """\
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:\
        print("\uc0\u10060  Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en variables de entorno.")\
        return\
\
    _sleep_until_next_slot()\
    url = f"https://api.telegram.org/bot\{TELEGRAM_TOKEN\}/sendMessage"\
    data = \{"chat_id": TELEGRAM_CHAT_ID, "text": text\}\
\
    attempt = 0\
    while attempt <= max_retries:\
        try:\
            r = requests.post(url, data=data, timeout=20)\
            if r.status_code == 429:\
                # Rate limit: respeta retry_after si viene\
                try:\
                    j = r.json()\
                    retry_after = j.get("parameters", \{\}).get("retry_after") or j.get("retry_after") or 3\
                except Exception:\
                    retry_after = 3\
                print(f"\uc0\u9203  Rate limit (429). Esperando \{retry_after\}s y reintentando\'85")\
                time.sleep(int(retry_after) + 1)\
                attempt += 1\
                continue\
\
            if not r.ok:\
                print("\uc0\u9888 \u65039  Error al enviar a Telegram:", r.status_code, r.text[:200])\
            return  # ok o error != 429: salimos\
        except Exception as e:\
            print("\uc0\u10060  Error conectando con Telegram:", e)\
            time.sleep(2)\
            attempt += 1\
\
def load_state():\
    try:\
        if os.path.exists(STATE_FILE):\
            with open(STATE_FILE, "r") as f:\
                return json.load(f)\
    except Exception:\
        pass\
    return \{"last_ts": 0, "seen_ids": [], "sent_raw_once": False\}\
\
def save_state(state):\
    try:\
        with open(STATE_FILE, "w") as f:\
            json.dump(state, f, indent=2)\
    except Exception as e:\
        print("\uc0\u9888 \u65039  No pude guardar estado:", e)\
\
def fmt_time(ts):\
    t = int(ts)\
    if t > 10_000_000_000:  # milisegundos\
        t = t / 1000\
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")\
\
def _normalize_fill(fill: dict):\
    """\
    Normaliza campos posibles en distintas respuestas.\
    """\
    coin = fill.get("coin") or fill.get("symbol") or fill.get("asset") or "?"\
    side = (fill.get("side") or fill.get("dir") or "?").upper()\
    px = fill.get("px") or fill.get("price") or fill.get("p") or "?"\
    sz = fill.get("sz") or fill.get("size") or fill.get("q") or "?"\
    tid = fill.get("tid") or fill.get("tradeId") or fill.get("id")\
    ts = fill.get("timestamp") or fill.get("ts") or fill.get("time") or 0\
    return \{"coin": coin, "side": side, "px": px, "sz": sz, "tid": tid, "ts": ts, "_raw": fill\}\
\
def _post_json(url, payload):\
    return requests.post(url, json=payload, timeout=30, headers=\{"Content-Type": "application/json"\})\
\
def fetch_fills_resilient(address: str, last_ts: int):\
    """\
    Intenta m\'faltiples variantes conocidas para obtener fills por usuario.\
    Devuelve lista de fills normalizados (posiblemente vac\'eda).\
    """\
    trials = [\
        ("POST userFills",      lambda: _post_json(HL_INFO_URL, \{"type": "userFills", "user": address\})),\
        ("POST fills",          lambda: _post_json(HL_INFO_URL, \{"type": "fills", "user": address\})),\
        ("POST fills+n",        lambda: _post_json(HL_INFO_URL, \{"type": "fills", "user": address, "n": 200\})),\
    ]\
\
    if last_ts and last_ts > 0:\
        trials.append(("POST fills+startTime", lambda: _post_json(HL_INFO_URL, \{"type": "fills", "user": address, "startTime": int(last_ts)\})))\
\
    trials.append(("GET userFills", lambda: requests.get(f"\{HL_INFO_URL\}?type=userFills&user=\{address\}", timeout=30)))\
\
    errors = []\
    for label, fn in trials:\
        try:\
            r = fn()\
            if r.ok:\
                data = r.json()\
                if isinstance(data, list):\
                    raw_fills = data\
                elif isinstance(data, dict):\
                    raw_fills = None\
                    for k, v in data.items():\
                        if isinstance(v, list):\
                            raw_fills = v\
                            break\
                    if raw_fills is None:\
                        errors.append(f"\{label\}: dict sin lista usable -> \{str(data)[:200]\}")\
                        continue\
                else:\
                    errors.append(f"\{label\}: respuesta no lista/dict -> \{type(data)\}")\
                    continue\
\
                fills = [_normalize_fill(x) for x in raw_fills]\
                if last_ts and last_ts > 0:\
                    fills = [f for f in fills if (f["ts"] and f["ts"] > last_ts)]\
                return fills\
\
            else:\
                errors.append(f"\{label\}: HTTP \{r.status_code\} -> \{r.text[:200]\}")\
        except Exception as e:\
            errors.append(f"\{label\}: EXC \{e\}")\
\
    if errors:\
        print("\uc0\u10060  No pude obtener fills. Detalle:")\
        for e in errors:\
            print("   -", e)\
    return []\
\
def build_message(addr: str, f: dict):\
    parts = []\
    parts.append("\uc0\u9889  Actividad detectada")\
    parts.append(f"Trader: `\{addr\}`")\
    if f.get("ts"):\
        parts.append(f"Hora: \{fmt_time(f['ts'])\}")\
    parts.append(f"Par: \{f.get('coin', '?')\}")\
    parts.append(f"Lado: \{f.get('side', '?')\}")\
    parts.append(f"Precio: \{f.get('px', '?')\}")\
    parts.append(f"Tama\'f1o: \{f.get('sz', '?')\}")\
    if f.get("tid") is not None:\
        parts.append(f"tradeId: \{f['tid']\}")\
    return "\\n".join(parts)\
\
def build_summary(addr: str, fills: list):\
    fills_sorted = sorted(fills, key=lambda x: x.get("ts", 0) or 0)\
    lines = [f"\uc0\u55357 \u56556  \{len(fills_sorted)\} eventos nuevos del trader `\{addr\}`"]\
    for f in fills_sorted[:5]:\
        ts = fmt_time(f.get("ts", 0) or 0)\
        coin = f.get("coin", "?")\
        side = f.get("side", "?")\
        sz = f.get("sz", "?")\
        px = f.get("px", "?")\
        lines.append(f"- [\{ts\}] \{coin\} \{side\} sz=\{sz\} px=\{px\}")\
    if len(fills_sorted) > 5:\
        lines.append(f"\'85 y \{len(fills_sorted) - 5\} m\'e1s.")\
    return "\\n".join(lines)\
\
def run_bot():\
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not HL_TRADER_ADDRESS:\
        print("\uc0\u10060  Faltan variables: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID / HL_TRADER_ADDRESS.")\
        return\
\
    print("\uc0\u9989  Iniciando bot HyperWhaleBot (Replit / rate-safe)\'85")\
    send_telegram("\uc0\u55357 \u56395  Bot iniciado. Monitoreo activo del trader en Hyperliquid.")\
\
    state = load_state()\
    last_ts = int(state.get("last_ts", 0))\
    seen_ids = set(state.get("seen_ids", []))\
    sent_raw_once = state.get("sent_raw_once", False)\
\
    while True:\
        try:\
            fills = fetch_fills_resilient(HL_TRADER_ADDRESS, last_ts)\
            new_items = []\
\
            if fills:\
                fills.sort(key=lambda f: f.get("ts", 0) or 0)\
                for f in fills:\
                    tid = f.get("tid")\
                    ts = f.get("ts", 0) or 0\
                    is_new_by_ts = (ts and ts > last_ts)\
                    is_new_by_id = (tid is not None and tid not in seen_ids)\
\
                    if not ts and tid is None:\
                        if not sent_raw_once:\
                            new_items.append(f)\
                            sent_raw_once = True\
                        continue\
\
                    if is_new_by_ts or is_new_by_id:\
                        new_items.append(f)\
                        if ts > last_ts:\
                            last_ts = ts\
                        if tid is not None:\
                            seen_ids.add(tid)\
\
            if new_items:\
                if len(new_items) > MAX_MSGS_PER_CYCLE:\
                    send_telegram(build_summary(HL_TRADER_ADDRESS, new_items))\
                else:\
                    for f in new_items:\
                        send_telegram(build_message(HL_TRADER_ADDRESS, f))\
\
                state["last_ts"] = int(last_ts)\
                state["seen_ids"] = list(seen_ids)[-MAX_SEEN_IDS:]\
                state["sent_raw_once"] = sent_raw_once\
                save_state(state)\
\
        except Exception as e:\
            print("\uc0\u10060  Error en loop:", e)\
\
        time.sleep(POLL_SECONDS)\
}