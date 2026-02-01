# app.py
import streamlit as st
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import urllib.parse
import base64

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
def parse_iso(dt_str: str) -> datetime:
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")

def price_link(label, price):
    try:
        p = float(price)
        return f"{label}: £{p:.2f}<br>"
    except Exception:
        return f"{label}: —<br>"

def safe_get(url, params=None):
    """Safe request with error handling."""
    try:
        r = requests.get(url, params=params, timeout=20,
                         headers={"Authorization": f"Bearer {POKEMON_API_KEY}"})
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if e.response.status_code == 400:
            st.warning("Search query too complex or invalid. Try simplifying it (e.g., just the card name).")
        else:
            st.error(f"API error: {e}")
        return []
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return []

# --------------------------
# POKEMON PRICE TRACKER API
# --------------------------
def search_card(query: str):
    if not query.strip():
        return []
    q = urllib.parse.quote(query.strip())
    payload = safe_get(f"{API_ROOT}/cards", params={"q": q})
    cards = payload.get("cards") or payload.get("data") or []

    # fallback: simplify query if nothing returned
    if not cards:
        simplified = query.strip().split()[0]
        q_simple = urllib.parse.quote(simplified)
        payload = safe_get(f"{API_ROOT}/cards", params={"q": q_simple})
        cards = payload.get("cards") or payload.get("data") or []

    return cards

def get_card_price(card_id: str):
    payload = safe_get(f"{API_ROOT}/cards/{card_id}")
    return payload or {}

def get_price_history(card_id: str, days: int = 30, limit: int = 20):
    payload = safe_get(f"{API_ROOT}/cards/{card_id}/history", params={"days": days, "limit": limit})
    if isinstance(payload, dict) and "history" in payload:
        payload = payload["history"]
    return (payload or [])[:limit]

# --------------------------
# eBay Browse API (LIVE listings)
# --------------------------
def get_ebay_token():
    """Client credentials token"""
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    encoded = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "Authorization": f"Basic {encoded}"}
    data = {"grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"}
    try:
        r = requests.post(url, headers=headers, data=data, timeout=20)
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        st.error(f"eBay auth error: {e}")
        return None

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
    try:
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
    except Exception as e:
        st.error(f"eBay search error: {e}")
        return []

# --------------------------
# SHOW CARD DATA
# --------------------------
def show_card_data(card_id: str, comps_limit: int, days: int):
    card_price = get_card_price(card_id)
    name         = card_price.get("name","Unknown")
    market_price = card_price.get("market_price",0)
    low_price    = card_price.get("low_price",0)
    high_price   = card_price.get("high_price",0)

    history = get_price_history(card_id, days=int(days), limit=int(comps_limit)) or []
    dates, prices = [], []
    for h in history:
        dt = parse_iso(h.get("date") or h.get("sold_at") or h.get("timestamp"))
        pr = h.get("price") or h.get("sold_price")
        if dt and pr is not None:
            dates.append(dt)
            prices.append(float(pr))

    # Sold HTML
    sold_html = (
        "<div>"
        + price_link("MARKET", market_price)
        + price_link("LOW", low_price)
        + price_link("HIGH", high_price)
        + f"VOLUME: {len(prices)}</div>"
    )

    # Plot
    sold_plot = None
    if dates:
        fig, ax = plt.subplots(figsize=(7,3.5))
        ax.scatter(dates, prices, color='green', s=40, label="Sold")
        if len(prices) > 1:
            x_nums = np.array([d.timestamp() for d in dates])
            y_vals = np.array(prices)
            x_norm = x_nums - x_nums.mean()
            coeffs = np.polyfit(x_norm, y_vals, 1)
            trend_y = np.polyval(coeffs, x_norm)
            ax.plot(dates, trend_y, color='orange', linestyle='--', linewidth=1.5, label="Trend")
        ax.axhline(float(market_price or 0), color='cyan', linestyle=':', linewidth=1, label=f"Market £{float(market_price or 0):.0f}")
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        fig.autofmt_xdate(rotation=30, ha='right')
        ax.set_ylabel("Price (£)")
        ax.legend()
        ax.set_title(f"Sold Prices: {name}")
        plt.tight_layout()
        sold_plot = fig

    sold_df = pd.DataFrame(
        [{"Price": p, "Date": d.strftime("%Y-%m-%d")} for p, d in sorted(zip(prices, dates), key=lambda t: t[1], reverse=True)]
    )

    # Live eBay listings
    live_items = get_ebay_live_listings(name, limit=10)
    live_html = "<div>"
    if live_items:
        for li in live_items:
            live_html += f"<a href='{li['url']}' target='_blank'>{li['title']}</a> - £{li['price']:.2f}<br>"
    else:
        live_html += "No live listings found"
    live_html += "</div>"

    return sold_html, sold_plot, sold_df, live_html

# --------------------------
# STREAMLIT UI
# --------------------------
st.title("Pokémon Price Tracker")

search_query = st.text_input("Search Pokémon card", "Pikachu")
comps_limit   = st.slider("Sold comps", 3, 50, DEFAULT_COMPS_LIMIT)
days_window   = st.radio("Search window (days)", ["30","60","90"], index=2)

if st.button("Search"):
    cards = search_card(search_query.strip())
    if not cards:
        st.warning("No cards found for that search.")
    else:
        selected = st.selectbox("Select card", [f"{c.get('name')} ({c.get('set_code')})" for c in cards])
        idx = [f"{c.get('name')} ({c.get('set_code')})" for c in cards].index(selected)
        card_id = cards[idx].get("id")
        sold_html, sold_plot, sold_df, live_html = show_card_data(card_id, comps_limit, int(days_window))
        
        st.markdown(sold_html, unsafe_allow_html=True)
        if sold_plot:
            st.pyplot(sold_plot)
        st.dataframe(sold_df)
        st.markdown(live_html, unsafe_allow_html=True)
