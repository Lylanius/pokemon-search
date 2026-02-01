# app.py
import streamlit as st
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone

# --------------------------
# CONFIG / SECRETS
# --------------------------
POKEMON_API_KEY = st.secrets["POKEMON_API_KEY"]
EBAY_APP_ID     = st.secrets["EBAY_APP_ID"]
EBAY_CERT_ID    = st.secrets["EBAY_CERT_ID"]
DEFAULT_COMPS_LIMIT = 20
API_ROOT = "https://www.pokemonpricetracker.com/api/v2"

# --------------------------
# HELPERS
# --------------------------
def auth_header():
    return {"Authorization": f"Bearer {POKEMON_API_KEY}"}

def parse_iso(dt_str):
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

# --------------------------
# POKEMON PRICE TRACKER API
# --------------------------
def search_card(query: str):
    r = requests.get(f"{API_ROOT}/cards", headers=auth_header(), params={"q": query}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    return payload.get("cards") or payload.get("data") or []

def get_card_price(card_id: str):
    r = requests.get(f"{API_ROOT}/cards/{card_id}", headers=auth_header(), timeout=20)
    r.raise_for_status()
    return r.json()

def get_price_history(card_id: str, days: int = 30, limit: int = 20):
    params = {"days": int(days), "limit": int(limit)}
    r = requests.get(f"{API_ROOT}/cards/{card_id}/history", headers=auth_header(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "history" in data:
        data = data["history"]
    return (data or [])[:limit]

# --------------------------
# EBAY BROWSE API
# --------------------------
import base64

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
# STREAMLIT UI
# --------------------------
st.set_page_config(page_title="Poké-Quant Tracker", layout="wide")

st.title("Poké-Quant Master Dashboard")

with st.sidebar:
    search_query = st.text_input("Search Card", value="Pikachu")
    comps_limit  = st.slider("Sold Comps", min_value=3, max_value=50, value=DEFAULT_COMPS_LIMIT)
    days_window  = st.radio("Search Window (Days)", options=[30,60,90], index=2)
    region_uk    = st.checkbox("UK only for eBay", value=True)
    search_btn   = st.button("Search Card")

if search_btn and search_query:
    cards = search_card(search_query.strip())
    if not cards:
        st.warning("No cards found!")
    else:
        # Display gallery of cards
        for idx, card in enumerate(cards[:12]):
            st.image(card.get("image", ""), width=120, caption=f"{card.get('name','Unknown')}")
        # Select first card by default
        card_id = cards[0]["id"]
        card_data = get_card_price(card_id)
        st.subheader(f"{card_data.get('name','Unknown')} - Market Info")
        st.write(f"Market Price: £{card_data.get('market_price',0):.2f}")
        st.write(f"Low Price: £{card_data.get('low_price',0):.2f}")
        st.write(f"High Price: £{card_data.get('high_price',0):.2f}")

        # Sold History
        history = get_price_history(card_id, days=int(days_window), limit=int(comps_limit))
        if history:
            dates = [parse_iso(h.get("date") or h.get("sold_at") or h.get("timestamp")) for h in history]
            prices = [h.get("price") or h.get("sold_price") for h in history]
            df = pd.DataFrame({"Date": dates, "Price (£)": prices})
            st.subheader("Sold History")
            st.dataframe(df.sort_values("Date", ascending=False))

            # Plot
            fig, ax = plt.subplots(figsize=(8,3.5))
            ax.scatter(dates, prices, color='green', label="Sold Prices")
            if len(prices) > 1:
                x_nums = np.array([d.timestamp() for d in dates])
                y_vals = np.array(prices)
                x_norm = x_nums - x_nums.mean()
                coeffs = np.polyfit(x_norm, y_vals, 1)
                trend_y = np.polyval(coeffs, x_norm)
                ax.plot(dates, trend_y, color='orange', linestyle='--', label="Trend")
            ax.set_ylabel("Price (£)")
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
            fig.autofmt_xdate(rotation=30)
            ax.legend()
            st.pyplot(fig)
        else:
            st.info("No sold history available.")

        # Live eBay listings
        st.subheader("Live eBay Listings")
        live_items = get_ebay_live_listings(card_data.get("name",""), limit=10, uk_only=region_uk)
        if live_items:
            for li in live_items:
                st.markdown(f"[{li['title']}]({li['url']}) - £{li['price']:.2f}")
        else:
            st.info("No live listings found.")
