# app.py
import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import numpy as np
import base64

# --------------------------
# CONFIG / SECRETS
# --------------------------
POKEMON_API_KEY = st.secrets["POKEMON_API_KEY"]
EBAY_APP_ID     = st.secrets["EBAY_APP_ID"]
EBAY_CERT_ID    = st.secrets["EBAY_CERT_ID"]
DEFAULT_COMPS_LIMIT = 20

# --------------------------
# HELPERS
# --------------------------
def _auth_header():
    return {"Authorization": f"Bearer {POKEMON_API_KEY}"}

def parse_iso(dt_str):
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except:
        return None

def price_link(label, price):
    try:
        p = float(price)
        return f"{label}: £{p:.2f}"
    except:
        return f"{label}: —"

# --------------------------
# POKEMON PRICE TRACKER API
# --------------------------
API_ROOT = "https://www.pokemonpricetracker.com/api/v2"

def search_card(query: str):
    """Search cards by name only (API does not like complex queries)."""
    if not query.strip():
        return []
    params = {"q": query.strip()}
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
# EBAY BROWSE API
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
# STREAMLIT UI
# --------------------------
st.set_page_config(page_title="Poké‑Quant Master", layout="wide")

st.title("Poké‑Quant Master 🔍")

with st.sidebar:
    search_query = st.text_input("Card Name", value="Pikachu")
    comps_limit  = st.slider("Number of Sold Comps", 3, 50, value=DEFAULT_COMPS_LIMIT)
    days_window  = st.select_slider("Search Window (Days)", options=[30, 60, 90], value=90)
    search_button = st.button("SEARCH CARD")

if search_button:
    try:
        cards = search_card(search_query)
        if not cards:
            st.warning("No cards found. Try a simpler search (just the card name).")
        else:
            # Let user pick card
            card_names = [c.get("name","Unknown") for c in cards]
            selected_card = st.selectbox("Select Card", card_names)
            card_idx = card_names.index(selected_card)
            card_id = cards[card_idx].get("id")
            
            # Get card price + sold history
            card_data = get_card_price(card_id)
            sold_history = get_price_history(card_id, days=days_window, limit=comps_limit)

            st.subheader("Card Info")
            st.write(f"**Name:** {card_data.get('name')}")
            st.write(price_link("Market Price", card_data.get("market_price")))
            st.write(price_link("Low Price", card_data.get("low_price")))
            st.write(price_link("High Price", card_data.get("high_price")))

            if sold_history:
                dates = [parse_iso(h.get("date") or h.get("sold_at") or h.get("timestamp")) for h in sold_history]
                prices = [float(h.get("price") or h.get("sold_price") or 0) for h in sold_history]

                st.subheader("Sold History")
                df = pd.DataFrame({"Date": [d.strftime("%Y-%m-%d") for d in dates], "Price (£)": prices})
                st.dataframe(df)

                # Plot
                fig, ax = plt.subplots(figsize=(8,4))
                ax.scatter(dates, prices, color='green', label="Sold")
                if len(prices) > 1:
                    x_nums = np.array([d.timestamp() for d in dates])
                    y_vals = np.array(prices)
                    x_norm = x_nums - x_nums.mean()
                    coeffs = np.polyfit(x_norm, y_vals, 1)
                    trend_y = np.polyval(coeffs, x_norm)
                    ax.plot(dates, trend_y, color='orange', linestyle='--', label="Trend")
                ax.axhline(float(card_data.get("market_price") or 0), color='cyan', linestyle=':', label="Market")
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
                fig.autofmt_xdate(rotation=30, ha='right')
                ax.set_ylabel("Price (£)")
                ax.legend()
                st.pyplot(fig)
            else:
                st.info("No sold history available for this card.")

            # Live eBay listings
            st.subheader("Live eBay Listings")
            live_items = get_ebay_live_listings(card_data.get("name"), limit=10)
            if live_items:
                for item in live_items:
                    st.markdown(f"[{item['title']}]({item['url']}) - £{item['price']:.2f}")
            else:
                st.info("No live listings found.")

    except requests.exceptions.HTTPError as e:
        st.error(f"API HTTP error: {e}")
    except Exception as e:
        st.error(f"Unexpected error: {e}")
