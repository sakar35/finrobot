"""
FinRobot v2 — Telegram Stock Tracking Bot
==========================================
Optimized for Render Free Tier + kvdb.io persistence.

Key upgrades over v1:
  • All secrets loaded from environment variables (no hardcoded tokens)
  • Proper logging (replaces bare print statements)
  • Batch price fetching with yf.download() — one API call for all symbols
  • Single DB write per price-check cycle (not one per symbol)
  • New commands: /ozet (P&L summary), /reset (re-arm alerts), /yardim (help)
  • Richer TP/SL alerts (shows actual P&L in currency)
  • Configurable price-check interval via PRICE_CHECK_INTERVAL env var
  • /health endpoint for Render uptime monitors
  • Cleaner command dispatch table (easy to extend)

Environment variables required:
  TELEGRAM_TOKEN        — Bot token from BotFather
  CHAT_ID               — Your Telegram user/group ID
  KVDB_URL              — Full kvdb.io key URL (e.g. https://kvdb.io/BUCKET/portfoy)
  PRICE_CHECK_INTERVAL  — (optional) seconds between price checks, default 60
"""

import time
import requests
import yfinance as yf
import os
import threading
import logging
from typing import Optional
from flask import Flask

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("FinRobot")

# ─── FLASK WEB SERVER ─────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    return "FinRobot v2 — Armored Engine Active ✅"

@app.route("/health")
def health():
    """Dedicated health-check endpoint for Render / UptimeRobot."""
    return {"status": "ok", "bot": "FinRobot", "version": "2.0"}, 200

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    log.info(f"🌐 Web server starting on port {port}")
    # Silence Flask's default werkzeug logs to keep console clean
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
    app.run(host="0.0.0.0", port=port)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN        = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID               = os.environ.get("CHAT_ID", "")
KVDB_URL              = os.environ.get("KVDB_URL", "")
PRICE_CHECK_INTERVAL  = int(os.environ.get("PRICE_CHECK_INTERVAL", "60"))

# ─── SHARED HTTP SESSION ──────────────────────────────────────────────────────
# Reusing one session avoids TCP handshake overhead on every call.
session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
})

# ─── DATABASE LAYER (kvdb.io) ─────────────────────────────────────────────────

def portfoy_oku() -> dict:
    """Read the entire portfolio from kvdb.io. Returns {} on any failure."""
    try:
        res = session.get(KVDB_URL, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning(f"DB read error: {e}")
    return {}


def portfoy_guncelle(portfoy: dict) -> bool:
    """Persist the portfolio to kvdb.io. Returns True on success."""
    try:
        res = session.put(KVDB_URL, json=portfoy, timeout=5)
        if res.status_code == 200:
            return True
        log.warning(f"DB write returned HTTP {res.status_code}")
    except Exception as e:
        log.warning(f"DB write error: {e}")
    return False

# ─── TELEGRAM LAYER ───────────────────────────────────────────────────────────

def telegram_mesaj_gonder(mesaj: str) -> bool:
    """Send a Markdown message to the configured Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}
    try:
        res = session.post(url, json=payload, timeout=5)
        return res.status_code == 200
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")
    return False

# ─── PRICE FETCHING ───────────────────────────────────────────────────────────

def fiyat_getir_tekli(sembol: str) -> Optional[float]:
    """
    Fetch latest closing price for a single symbol.
    Returns None (not a fallback default) when unavailable.
    """
    try:
        hisse = yf.Ticker(sembol, session=session)
        hist = hisse.history(period="5d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception as e:
        log.warning(f"Single price fetch failed [{sembol}]: {e}")
    return None


def toplu_fiyat_getir(semboller: list[str]) -> dict[str, float]:
    """
    Batch-fetch latest closing prices for multiple symbols in ONE yfinance call.
    Falls back to individual fetches if the batch call fails entirely.
    Returns {SYMBOL: latest_price}.
    """
    if not semboller:
        return {}

    prices: dict[str, float] = {}

    try:
        veri = yf.download(
            semboller,
            period="5d",
            auto_adjust=True,
            progress=False,
            threads=True,
            timeout=15,
        )
        if veri.empty:
            raise ValueError("yf.download returned empty DataFrame")

        close = veri["Close"]

        if len(semboller) == 1:
            # Single-symbol download returns a flat Series, not a named column
            clean = close.dropna()
            if not clean.empty:
                prices[semboller[0]] = round(float(clean.iloc[-1]), 2)
        else:
            for sembol in semboller:
                if sembol in close.columns:
                    col = close[sembol].dropna()
                    if not col.empty:
                        prices[sembol] = round(float(col.iloc[-1]), 2)

        log.info(f"Batch price fetch OK — {len(prices)}/{len(semboller)} symbols")

    except Exception as e:
        log.warning(f"Batch price fetch failed: {e} — falling back to individual fetches")
        for sembol in semboller:
            p = fiyat_getir_tekli(sembol)
            if p is not None:
                prices[sembol] = p

    return prices

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def para_birimi(sembol: str) -> str:
    """Return 'TL' for BIST (.IS) symbols, '$' for everything else."""
    return "TL" if sembol.endswith(".IS") else "$"

# ─── COMMAND HANDLERS ─────────────────────────────────────────────────────────

def cmd_ekle(parcalar: list[str], portfoy: dict) -> str:
    """
    /ekle SYMBOL LOT TP% SL%
    Adds a new position. Fetches live entry price automatically.
    """
    if len(parcalar) != 5:
        return (
            "⚠️ *Wrong format.* Usage:\n"
            "`/ekle SYMBOL LOT TP% SL%`\n\n"
            "Examples:\n"
            "`/ekle TCELL.IS 50 10 5`\n"
            "`/ekle AAPL 10 15 7`"
        )
    try:
        sembol    = parcalar[1].upper()
        lot       = float(parcalar[2])
        tp_pct    = float(parcalar[3]) / 100.0
        sl_pct    = float(parcalar[4]) / 100.0
    except ValueError:
        return "⚠️ Lot, TP%, and SL% must be numeric. Example: `/ekle THYAO.IS 100 12 6`"

    if lot <= 0 or tp_pct <= 0 or sl_pct <= 0:
        return "⚠️ Lot, TP%, and SL% must all be positive numbers."

    telegram_mesaj_gonder(f"🔍 Fetching live price for *{sembol}*…")

    pb      = para_birimi(sembol)
    maliyet = fiyat_getir_tekli(sembol)
    uyari   = ""

    if maliyet is None:
        maliyet = 100.0
        uyari   = "\n\n⚠️ _Could not reach Yahoo Finance. Cost defaulted to 100. Use `/guncelle {sembol} <price>` to correct._"

    tp_fiyat = round(maliyet * (1 + tp_pct), 2)
    sl_fiyat = round(maliyet * (1 - sl_pct), 2)

    portfoy[sembol] = {
        "lot":           lot,
        "maliyet":       maliyet,
        "tp_fiyat":      tp_fiyat,
        "sl_fiyat":      sl_fiyat,
        "tetiklendi_tp": False,
        "tetiklendi_sl": False,
        "son_fiyat":     maliyet,
    }
    portfoy_guncelle(portfoy)

    return (
        f"✅ *{sembol} added to portfolio!*\n"
        f"─────────────────────────\n"
        f"📦 Quantity : {lot:.0f} Lot\n"
        f"💰 Entry    : {maliyet:.2f} {pb}\n"
        f"🎯 TP Target: {tp_fiyat:.2f} {pb}  (+{tp_pct*100:.1f}%)\n"
        f"🛑 SL Target: {sl_fiyat:.2f} {pb}  (-{sl_pct*100:.1f}%)"
        f"{uyari}"
    )


def cmd_guncelle(parcalar: list[str], portfoy: dict) -> str:
    """
    /guncelle SYMBOL PRICE
    Manually override the entry cost for a symbol (useful when Yahoo Finance
    fails during /ekle and defaults to 100).
    """
    if len(parcalar) != 3:
        return "⚠️ Usage: `/guncelle TCELL.IS 45.20`"
    sembol = parcalar[1].upper()
    if sembol not in portfoy:
        return f"⚠️ *{sembol}* is not in your portfolio."
    try:
        yeni_fiyat = float(parcalar[2])
    except ValueError:
        return "⚠️ Price must be a number."
    if yeni_fiyat <= 0:
        return "⚠️ Price must be positive."

    v = portfoy[sembol]
    pb = para_birimi(sembol)
    # Recalculate TP/SL proportionally around the new cost
    tp_pct = (v["tp_fiyat"] / v["maliyet"]) - 1
    sl_pct = 1 - (v["sl_fiyat"] / v["maliyet"])
    v["maliyet"]   = yeni_fiyat
    v["tp_fiyat"]  = round(yeni_fiyat * (1 + tp_pct), 2)
    v["sl_fiyat"]  = round(yeni_fiyat * (1 - sl_pct), 2)
    v["son_fiyat"] = yeni_fiyat
    portfoy_guncelle(portfoy)
    return (
        f"✏️ *{sembol}* entry cost updated.\n"
        f"💰 Cost: {yeni_fiyat:.2f} {pb}\n"
        f"🎯 TP  : {v['tp_fiyat']:.2f} {pb}\n"
        f"🛑 SL  : {v['sl_fiyat']:.2f} {pb}"
    )


def cmd_liste(portfoy: dict) -> str:
    """
    /liste
    Detailed table of every open position.
    """
    if not portfoy:
        return "📭 Portfolio is empty. Use `/ekle` to add a stock."

    tekst = "📋 *Portfolio — Open Positions*\n" + "─" * 30 + "\n\n"
    for sembol, v in portfoy.items():
        pb    = para_birimi(sembol)
        lot   = v.get("lot", 1)
        mal   = v["maliyet"]
        son   = v.get("son_fiyat", mal)
        net   = (son - mal) * lot
        pct   = ((son - mal) / mal * 100) if mal else 0
        deger = son * lot

        yon   = "🟢" if pct >= 0 else "🔴"
        pct_s = f"+{pct:.2f}" if pct >= 0 else f"{pct:.2f}"
        tp_ic = " ✅" if v.get("tetiklendi_tp") else ""
        sl_ic = " 🚨" if v.get("tetiklendi_sl") else ""

        tekst += (
            f"📌 *{sembol}*{tp_ic}{sl_ic}\n"
            f"   {lot:.0f} Lot @ {mal:.2f} → {son:.2f} {pb}\n"
            f"   {yon} {pct_s}%  |  Net: {net:+.2f} {pb}\n"
            f"   Value: {deger:,.2f} {pb}\n\n"
        )
    return tekst.rstrip()


def cmd_ozet(portfoy: dict) -> str:
    """
    /ozet
    Aggregate P&L broken down by currency (TL / USD).
    """
    if not portfoy:
        return "📭 Portfolio is empty."

    tl_cost = tl_cur = usd_cost = usd_cur = 0.0
    for sembol, v in portfoy.items():
        lot = v.get("lot", 1)
        mal = v["maliyet"]
        son = v.get("son_fiyat", mal)
        if sembol.endswith(".IS"):
            tl_cost  += mal * lot
            tl_cur   += son * lot
        else:
            usd_cost += mal * lot
            usd_cur  += son * lot

    def blok(baslik: str, pb: str, cost: float, cur: float) -> str:
        pnl = cur - cost
        pct = (pnl / cost * 100) if cost else 0
        yon = "🟢" if pnl >= 0 else "🔴"
        return (
            f"{baslik}\n"
            f"   Cost    : {cost:>12,.2f} {pb}\n"
            f"   Current : {cur:>12,.2f} {pb}\n"
            f"   {yon} P&L : {pnl:>+12,.2f} {pb}  ({pct:+.2f}%)\n\n"
        )

    tekst = "📊 *Portfolio Summary*\n" + "─" * 30 + "\n\n"
    if tl_cost > 0:
        tekst += blok("🇹🇷 *BIST (TL)*", "TL", tl_cost, tl_cur)
    if usd_cost > 0:
        tekst += blok("🇺🇸 *US Equities ($)*", "$", usd_cost, usd_cur)
    if tl_cost == 0 and usd_cost == 0:
        tekst += "_No positions with valid data._"
    return tekst.rstrip()


def cmd_sil(parcalar: list[str], portfoy: dict) -> str:
    """
    /sil SYMBOL
    Remove a position from the portfolio.
    """
    if len(parcalar) != 2:
        return "⚠️ Usage: `/sil TCELL.IS`"
    sembol = parcalar[1].upper()
    if sembol not in portfoy:
        return f"⚠️ *{sembol}* is not in your portfolio."
    del portfoy[sembol]
    portfoy_guncelle(portfoy)
    return f"🗑️ *{sembol}* removed from portfolio."


def cmd_reset(parcalar: list[str], portfoy: dict) -> str:
    """
    /reset SYMBOL
    Clear TP and SL triggered flags so alerts fire again.
    """
    if len(parcalar) != 2:
        return "⚠️ Usage: `/reset TCELL.IS`"
    sembol = parcalar[1].upper()
    if sembol not in portfoy:
        return f"⚠️ *{sembol}* is not in your portfolio."
    portfoy[sembol]["tetiklendi_tp"] = False
    portfoy[sembol]["tetiklendi_sl"] = False
    portfoy_guncelle(portfoy)
    return f"🔄 Alerts re-armed for *{sembol}*. Monitoring resumed."


def cmd_yardim() -> str:
    """
    /yardim  (also /help and /start)
    Show the command reference.
    """
    return (
        "🤖 *FinRobot v2 — Commands*\n"
        "─────────────────────────────\n"
        "*/ekle* `SYMBOL LOT TP% SL%`\n"
        "  Add a position with auto entry price\n"
        "  _e.g. /ekle TCELL.IS 50 10 5_\n\n"
        "*/liste* — All positions (detailed)\n\n"
        "*/ozet* — Portfolio P&L summary\n\n"
        "*/guncelle* `SYMBOL PRICE`\n"
        "  Override entry cost manually\n\n"
        "*/sil* `SYMBOL` — Remove a position\n\n"
        "*/reset* `SYMBOL` — Re-arm TP/SL alerts\n\n"
        "*/yardim* — This help message\n"
        "─────────────────────────────\n"
        "📌 BIST tickers need `.IS` suffix\n"
        "   e.g. `GARAN.IS`, `THYAO.IS`\n"
        "📌 US tickers use plain symbol\n"
        "   e.g. `AAPL`, `NVDA`, `SPY`"
    )


# ─── COMMAND DISPATCH TABLE ───────────────────────────────────────────────────
# Maps each command string to a handler callable.
# Handlers that need the portfolio receive (parcalar, portfoy).
# Handlers that don't are wrapped with a lambda below.

COMMANDS = {
    "/ekle":      lambda p, pf: cmd_ekle(p, pf),
    "/liste":     lambda p, pf: cmd_liste(pf),
    "/ozet":      lambda p, pf: cmd_ozet(pf),
    "/guncelle":  lambda p, pf: cmd_guncelle(p, pf),
    "/sil":       lambda p, pf: cmd_sil(p, pf),
    "/reset":     lambda p, pf: cmd_reset(p, pf),
    "/yardim":    lambda p, pf: cmd_yardim(),
    "/help":      lambda p, pf: cmd_yardim(),
    "/start":     lambda p, pf: cmd_yardim(),
}

# ─── MAIN BOT LOOP ────────────────────────────────────────────────────────────

def bot_ana_dongu():
    log.info("🚀 FinRobot v2 engine started.")
    last_update_id      = 0
    last_price_check    = 0.0

    while True:
        # ── BLOCK 1: Telegram command polling ────────────────────────────
        try:
            url = (
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
                f"/getUpdates?offset={last_update_id + 1}&timeout=5"
            )
            yanit = session.get(url, timeout=10).json()

            for update in yanit.get("result", []):
                last_update_id = update["update_id"]
                mesaj_metni = update.get("message", {}).get("text", "").strip()
                if not mesaj_metni or not mesaj_metni.startswith("/"):
                    continue

                log.info(f"Incoming: {mesaj_metni!r}")
                parcalar = mesaj_metni.split()
                # Strip @BotName suffix (e.g. /liste@FinRobotBot → /liste)
                komut = parcalar[0].lower().split("@")[0]

                portfoy = portfoy_oku()
                handler = COMMANDS.get(komut)

                if handler:
                    cevap = handler(parcalar, portfoy)
                else:
                    cevap = "❓ Unknown command. Type `/yardim` for the full list."

                telegram_mesaj_gonder(cevap)

        except Exception as e:
            log.warning(f"Telegram polling bypassed: {e}")

        # ── BLOCK 2: Periodic price check & TP/SL alerts ─────────────────
        if time.time() - last_price_check >= PRICE_CHECK_INTERVAL:
            last_price_check = time.time()
            try:
                portfoy   = portfoy_oku()
                semboller = list(portfoy.keys())

                if not semboller:
                    time.sleep(1)
                    continue

                log.info(f"Price check [{len(semboller)} symbols]: {semboller}")
                fiyatlar       = toplu_fiyat_getir(semboller)
                portfoy_dirty  = False  # only write once at the end

                for sembol, veri in portfoy.items():
                    guncel = fiyatlar.get(sembol)
                    if guncel is None:
                        log.warning(f"No price for {sembol} — skipped.")
                        continue

                    veri["son_fiyat"] = guncel
                    portfoy_dirty     = True
                    pb                = para_birimi(sembol)
                    lot               = veri.get("lot", 1)
                    kar_zarar         = (guncel - veri["maliyet"]) * lot

                    # Take Profit
                    if guncel >= veri["tp_fiyat"] and not veri.get("tetiklendi_tp", False):
                        veri["tetiklendi_tp"] = True
                        telegram_mesaj_gonder(
                            f"🎯 *TAKE PROFIT HIT!*\n"
                            f"📌 *{sembol}* @ {guncel:.2f} {pb}\n"
                            f"💰 Profit: +{kar_zarar:,.2f} {pb}  ({lot:.0f} Lot)\n"
                            f"_Type /reset {sembol} to re-arm_"
                        )
                        log.info(f"TP triggered: {sembol} @ {guncel}")

                    # Stop Loss
                    elif guncel <= veri["sl_fiyat"] and not veri.get("tetiklendi_sl", False):
                        veri["tetiklendi_sl"] = True
                        telegram_mesaj_gonder(
                            f"🛑 *STOP LOSS HIT!*\n"
                            f"📌 *{sembol}* @ {guncel:.2f} {pb}\n"
                            f"📉 Loss: {kar_zarar:,.2f} {pb}  ({lot:.0f} Lot)\n"
                            f"_Type /reset {sembol} to re-arm_"
                        )
                        log.info(f"SL triggered: {sembol} @ {guncel}")

                # ── Single DB write for the whole cycle ──
                if portfoy_dirty:
                    portfoy_guncelle(portfoy)

            except Exception as e:
                log.warning(f"Price check bypassed: {e}")

        time.sleep(1)


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Validate required env vars before starting
    missing = [v for v in ("TELEGRAM_TOKEN", "CHAT_ID", "KVDB_URL") if not os.environ.get(v)]
    if missing:
        log.error(f"❌ Missing environment variables: {', '.join(missing)}")
        log.error("Set them in Render → Environment → Add Environment Variable.")
        raise SystemExit(1)

    # Start Flask in a daemon thread (dies with main thread)
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    bot_ana_dongu()
