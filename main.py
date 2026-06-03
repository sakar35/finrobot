import time
import requests
import yfinance as yf
import json
import os
import threading
from flask import Flask

# --- RENDER İÇİN MİNİK WEB SUNUCUSU ---
app = Flask('')
@app.route('/')
def home():
    return "FinRobot Zırhlı Motor Aktif!"

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- BOTUN ASIL KODLARI ---
TELEGRAM_TOKEN = "8845362119:AAFpplIh9vLfBWALpS7A7ZxvSurS4AZ19q0" # Tokenini buraya tam ekle
CHAT_ID = "2142625922" # ID'ni buraya yaz
KVDB_URL = "https://kvdb.io/FinRobotSecureStorage_9921a/portfoy"

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})

def portfoy_guncelle(portfoy):
    try: requests.put(KVDB_URL, json=portfoy, timeout=5)
    except: print("Veritabanı yazma hatası.")

def portfoy_oku():
    try:
        res = requests.get(KVDB_URL, timeout=5)
        if res.status_code == 200: return res.json()
        return {}
    except: return {}

def telegram_mesaj_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mesaj, "parse_mode": "Markdown"}
    try: requests.post(url, json=payload, timeout=5)
    except: print("Telegram mesajı gönderilemedi.")

def bot_ana_dongu():
    print("🚀 FinRobot Engelsiz Sunucu Motoru Aktif...")
    last_update_id = 0
    last_price_check_time = 0 
    
    while True:
        # 1. Telegram Komutlarını Dinle (Tamamen korumalı blok)
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=5"
            yanit = requests.get(url, timeout=5).json()
            
            for update in yanit.get("result", []):
                last_update_id = update["update_id"]
                mesaj_metni = update.get("message", {}).get("text", "")
                portfoy = portfoy_oku()
                
                if mesaj_metni.startswith("/ekle"):
                    parcalar = mesaj_metni.split()
                    if len(parcalar) == 5:
                        sembol = parcalar[1].upper()
                        lot_sayisi = float(parcalar[2])
                        hedef_yuzde = float(parcalar[3]) / 100
                        stop_yuzde = float(parcalar[4]) / 100
                        
                        telegram_mesaj_gonder(f"🔍 {sembol} fiyatı çekiliyor...")
                        
                        # Yahoo Finance hataya düşse bile botun çökmesini önlüyoruz
                        try:
                            hisse = yf.Ticker(sembol, session=session)
                            veri_tarihcesi = hisse.history(period="5d")
                            if not veri_tarihcesi.empty:
                                otomatik_maliyet = round(veri_tarihcesi['Close'].iloc[-1], 2)
                            else:
                                otomatik_maliyet = 100.0  # Veri çekilemezse bot kilitlenmesin diye varsayılan değer
                        except:
                            otomatik_maliyet = 100.0
                            telegram_mesaj_gonder("⚠️ Yahoo Finance'e şu an bağlanılamadı, varsayılan maliyet 100 TL girildi.")
                        
                        portfoy[sembol] = {
                            "lot": lot_sayisi,
                            "maliyet": otomatik_maliyet,
                            "tp_fiyat": otomatik_maliyet * (1 + hedef_yuzde),
                            "sl_fiyat": otomatik_maliyet * (1 - stop_yuzde),
                            "tetiklendi_tp": False,
                            "tetiklendi_sl": False,
                            "son_fiyat": otomatik_maliyet
                        }
                        portfoy_guncelle(portfoy)
                        pb = "$" if not sembol.endswith(".IS") else "TL"
                        
                        cevap = f"✅ *{sembol} Eklendi!*\n📦 {lot_sayisi:.0f} Lot\n💰 Maliyet: {otomatik_maliyet:.2f} {pb}"
                        telegram_mesaj_gonder(cevap)
                    else:
                        telegram_mesaj_gonder("⚠️ Format: `/ekle TCELL.IS 50 10 5`")
                
                elif mesaj_metni == "/liste":
                    if not portfoy:
                        telegram_mesaj_gonder("📭 Veritabanınız boş.")
                    else:
                        liste_metni = "📋 *Portföy Durumu:*\n\n"
                        for k, v in portfoy.items():
                            pb = "$" if not k.endswith(".IS") else "TL"
                            lot = v.get("lot", 1)
                            mal = v["maliyet"]
                            son = v.get("son_fiyat", mal)
                            toplam_guncel = son * lot
                            net_pnl = toplam_guncel - (mal * lot)
                            kz_yuzde = ((son - mal) / mal) * 100
                            durum = f"🟢 +%{kz_yuzde:.2f}" if kz_yuzde >= 0 else f"🔴 -%{abs(kz_yuzde):.2f}"
                            
                            liste_metni += f"📌 *{k}* -> {lot:.0f} Lot ({durum})\n"
                            liste_metni += f"💰 Mal: {mal:.2f} {pb} | Güncel: {son:.2f} {pb}\n"
                            liste_metni += f"📊 Değer: {toplam_guncel:.2f} {pb} | Net: {net_pnl:+.2f} {pb}\n\n"
                        telegram_mesaj_gonder(liste_metni)
                        
                elif mesaj_metni.startswith("/sil"):
                    parcalar = mesaj_metni.split()
                    if len(parcalar) == 2 and parcalar[1].upper() in portfoy:
                        del portfoy[parcalar[1].upper()]
                        portfoy_guncelle(portfoy)
                        telegram_mesaj_gonder("🗑️ Silindi.")
        except Exception as e:
            print(f"Telegram dinleme hatası bypass edildi: {e}")

        # 2. Fiyatları Kontrol Et (60 Saniyede Bir)
        guncel_zaman = time.time()
        if guncel_zaman - last_price_check_time >= 60:
            last_price_check_time = guncel_zaman
            try:
                portfoy = portfoy_oku()
                for sembol, veri in portfoy.items():
                    try:
                        hisse = yf.Ticker(sembol, session=session)
                        veri_tarihcesi = hisse.history(period="5d")
                        if veri_tarihcesi.empty: continue
                        
                        guncel_fiyat = veri_tarihcesi['Close'].iloc[-1]
                        veri["son_fiyat"] = guncel_fiyat
                        portfoy_guncelle(portfoy)
                        
                        pb = "$" if not sembol.endswith(".IS") else "TL"
                        
                        if guncel_fiyat >= veri["tp_fiyat"] and not veri.get("tetiklendi_tp", False):
                            telegram_mesaj_gonder(f"🎯 *HEDEF!* \n*{sembol}*: {guncel_fiyat:.2f} {pb}")
                            veri["tetiklendi_tp"] = True
                            portfoy_guncelle(portfoy)
                        elif guncel_fiyat <= veri["sl_fiyat"] and not veri.get("tetiklendi_sl", False):
                            telegram_mesaj_gonder(f"🛑 *STOP!* \n*{sembol}*: {guncel_fiyat:.2f} {pb}")
                            veri["tetiklendi_sl"] = True
                            portfoy_guncelle(portfoy)
                    except:
                        pass # Tekil hisse hatası ana döngüyü bozmasın
            except:
                pass
                    
        time.sleep(1)

if __name__ == "__main__":
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()
    bot_ana_dongu()