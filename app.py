import os
import base64
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone
import streamlit as st

# --------------------------
# CONFIG / SECRETS
# --------------------------
POKEMON_API_KEY = st.secrets["POKEMON_API_KEY"]
EBAY_APP_ID     = st.secrets["EBAY_APP_ID"]
EBAY_CERT_ID    = st.secrets["EBAY_CERT_ID"]
DEFAULT_COMPS_LIMIT = 20

API_ROOT = "https://www.pokemonpricetracker.com/api/v2"

# --------------------------
# API HELPERS
# --------------------------
def _auth_header():
    return {"Authorization": f"Bearer {POKEMON_API_KEY}"}

def search_card(name: str, set_code: str = "", rarity: str = ""):
    """Search Pokemon Price Tracker API with optional set and rarity filters"""
    params = {"q": name}
    if set_code:
        params["set"] = set_code
    if rarity:
        params["rarity"] = rarity
    r = requests.get(f"{API_ROOT}/cards", headers=_auth_header(), params=params, timeout=20)
    r.raise_for_status()
    payload = r.json()
    return payload.get("cards") or payload.get("data") or []

def get_card_price(card_id: str):
    r = requests.get(f"{API_ROOT}/cards/{card_id}", headers=_auth_header(), timeout=20)
    r.raise_for_status()
    return r.json()

def get_price_history(card_id: str, days: int = 30, limit: int = 20):
    params = {"days": int(days), "limit": int(limit)}
    r = requests.get(f"{API_ROOT}/cards/{card_id}/history", headers=_auth_header(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "history" in data:
        data = data["history"]
    return (data or [])[:limit]

# --------------------------
# eBay Live Listings
# --------------------------
def get_ebay_token():
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    encoded = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "Authorization": f"Basic {encoded}"}
    data = {"grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"}
    r = requests.post(url, headers=headers, data=data, timeout=20)
    r.raise_for_status()
    return r.json().get("access_token")

def get_ebay_live_listings(keywords: str, limit: int = 10, uk_only: bool = True):
    token = get_ebay_token()
    if not token:
        return []
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB" if uk_only else "EBAY_US"
    }
    params = {"q": keywords, "limit": limit}
    if uk_only:
        params["filter"] = "itemLocationCountry:GB"
    r = requests.get("https://api.ebay.com/buy/browse/v1/item_summary/search",
                     headers=headers, params=params, timeout=20)
    r.raise_for_status()
    items = []
    for i in r.json().get("itemSummaries", []):
        try:
            price = float(i["price"]["value"])
        except Exception:
            continue
        items.append({
            "title": i.get("title",""),
            "price": price,
            "url":   i.get("itemWebUrl") or i.get("itemAffiliateWebUrl","")
        })
    return items

# --------------------------
# UTILS
# --------------------------
def parse_iso(dt_str: str):
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except:
        return None

def fmt_date(dt_str: str):
    try:
        return parse_iso(dt_str).strftime("%d %b %Y")
    except:
        return str(dt_str)

# --------------------------
# STREAMLIT UI
# --------------------------
st.set_page_config(page_title="Poké-Quant Tracker", layout="wide")

st.title("Poké-Quant Tracker 🔥")

with st.sidebar:
    st.header("Search Filters")
    card_name = st.text_input("Card Name", "Charizard")
    set_code  = st.text_input("Set Code (optional)", "")
    rarity    = st.selectbox("Rarity (optional)", ["", "Common", "Uncommon", "Rare", "Ultra Rare", "Secret Rare"])
    comps_limit = st.slider("Sold Comps Limit", 3, 50, DEFAULT_COMPS_LIMIT)
    days_window = st.radio("Search Window (Days)", ["30", "60", "90"], index=2)
    uk_only    = st.checkbox("UK Only (eBay)", True)
    search_btn = st.button("Search Cards")

if search_btn:
    try:
        # 1) Search card
        cards = search_card(card_name.strip(), set_code.strip(), rarity)
        if not cards:
            st.warning("No cards found. Try simplifying the search query (just card name).")
        else:
            # show first few card results
            st.subheader("Results")
            for c in cards[:10]:
                st.image(c.get("image","https://via.placeholder.com/150"), width=120)
                st.markdown(f"**{c.get('name','Unknown')}** - £{c.get('market_price',0):.2f}")
                st.markdown(f"_Set: {c.get('set','')} | Rarity: {c.get('rarity','')}_")
                card_id = c.get("id")

                # 2) Sold history
                history = get_price_history(card_id, days=int(days_window), limit=int(comps_limit))
                if history:
                    dates = [parse_iso(h.get("date") or h.get("sold_at") or h.get("timestamp")) for h in history]
                    prices = [h.get("price") or h.get("sold_price") for h in history]

                    fig, ax = plt.subplots(figsize=(8,3.5))
                    ax.scatter(dates, prices, color='#00ff00', s=40)
                    if len(prices) > 1:
                        x_nums = np.array([d.timestamp() for d in dates])
                        y_vals = np.array(prices)
                        x_norm = x_nums - x_nums.mean()
                        coeffs = np.polyfit(x_norm, y_vals, 1)
                        trend_y = np.polyval(coeffs, x_norm)
                        ax.plot(dates, trend_y, color='#ffaa00', linestyle='--', linewidth=1.5)
                    ax.set_title(f"Sold Prices: {c.get('name')}")
                    ax.set_ylabel("Price (£)")
                    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
                    fig.autofmt_xdate()
                    st.pyplot(fig)
                else:
                    st.info("No sold history available.")

                # 3) Live eBay listings
                live_items = get_ebay_live_listings(c.get("name"), limit=5, uk_only=uk_only)
                if live_items:
                    st.markdown("**Live eBay Listings:**")
                    for li in live_items:
                        st.markdown(f"- [{li['title']}]({li['url']}) - £{li['price']:.2f}")
                else:
                    st.info("No live eBay listings found.")

    except requests.HTTPError as e:
        st.error(f"API HTTP error: {e}")
    except Exception as e:
        st.error(f"Unexpected error: {e}")
