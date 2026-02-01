# app.py
import streamlit as st
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

# --------------------------
# Load secrets
# --------------------------
POKEMON_API_KEY = st.secrets.get("POKEMON_API_KEY", "")
EBAY_APP_ID     = st.secrets.get("EBAY_APP_ID", "")
EBAY_CERT_ID    = st.secrets.get("EBAY_CERT_ID", "")
API_ROOT = "https://www.pokemonpricetracker.com/api/v2"
DEFAULT_COMPS_LIMIT = 20

# --------------------------
# Helpers
# --------------------------
def auth_header():
    if not POKEMON_API_KEY:
        raise ValueError("Missing Pokémon API key — add to Streamlit Secrets")
    return {"Authorization": f"Bearer {POKEMON_API_KEY}"}

def parse_iso(dt_str):
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except:
        return None

# --------------------------
# API functions
# --------------------------
def safe_get(url, params=None):
    """Make a GET request and return JSON or error."""
    try:
        response = requests.get(url, headers=auth_header(), params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as e:
        st.error(f"🛑 API HTTP error: {e}")
        return None
    except Exception as e:
        st.error(f"🛑 Request failed: {e}")
        return None

def search_cards(query):
    return safe_get(f"{API_ROOT}/cards", params={"q": query}) or []

def get_price_details(card_id):
    return safe_get(f"{API_ROOT}/cards/{card_id}")

def get_price_history(card_id, days, limit):
    return safe_get(f"{API_ROOT}/cards/{card_id}/history", params={"days": days, "limit": limit}) or []

# --------------------------
# UI
# --------------------------
st.title("Poké‑Quant Price Tracker")

# Sidebar controls
with st.sidebar:
    search_input = st.text_input("Search Card", value="Pikachu")
    comps_limit  = st.slider("Sold Comps", 3, 50, DEFAULT_COMPS_LIMIT)
    days_window  = st.radio("SEARCH WINDOW (DAYS)", [30,60,90], index=2)
    search_button = st.button("Search")

if search_button:
    if not search_input.strip():
        st.warning("Please enter a card name to search.")
    else:
        cards_data = search_cards(search_input.strip())
        
        # If the API returned a dict with cards inside
        cards = cards_data.get("cards") or cards_data.get("data") if isinstance(cards_data, dict) else cards_data

        if not cards:
            st.info("No cards found with that query.")
        else:
            # Show search results
            st.subheader("Search Results")
            for card in cards[:8]:
                st.image(card.get("image",""), width=120, caption=card.get("name",""))

            selected = cards[0]  # default to first result
            card_id = selected.get("id")

            # Price overview
            price_info = get_price_details(card_id)
            if price_info:
                st.markdown(f"### {price_info.get('name','Unknown')}")
                st.write(f"**Market**: £{price_info.get('market_price',0):.2f}")
                st.write(f"**Low**: £{price_info.get('low_price',0):.2f}")
                st.write(f"**High**: £{price_info.get('high_price',0):.2f}")

            # Sold history
            history = get_price_history(card_id, days_window, comps_limit)
            if history:
                dates, prices = [], []
                for h in history:
                    dt = parse_iso(h.get("date"))
                    pr = h.get("price")
                    if dt and pr is not None:
                        dates.append(dt)
                        prices.append(pr)

                if dates:
                    st.subheader("Sold Price History")
                    df = pd.DataFrame({"Date": dates, "Price (£)": prices})
                    st.dataframe(df.sort_values("Date", ascending=False))

                    fig, ax = plt.subplots(figsize=(7,3))
                    ax.scatter(dates, prices, color="blue")
                    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
                    st.pyplot(fig)
            else:
                st.info("No sold history available.")

            # Live eBay (optional)
            if EBAY_APP_ID and EBAY_CERT_ID:
                import base64
                def get_ebay_token():
                    url = "https://api.ebay.com/identity/v1/oauth2/token"
                    encoded = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
                    headers = {"Content-Type":"application/x-www-form-urlencoded",
                               "Authorization": f"Basic {encoded}"}
                    data={"grant_type":"client_credentials","scope":"https://api.ebay.com/oauth/api_scope"}
                    try:
                        r = requests.post(url, headers=headers, data=data, timeout=10)
                        r.raise_for_status()
                        return r.json().get("access_token")
                    except:
                        return None

                token = get_ebay_token()
                if token:
                    ebay_listings = []
                    try:
                        headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID":"EBAY_GB"}
                        r = requests.get("https://api.ebay.com/buy/browse/v1/item_summary/search",
                                         headers=headers, params={"q": price_info.get("name",""), "limit":10}, timeout=15)
                        r.raise_for_status()
                        for item in r.json().get("itemSummaries", []):
                            price_val = item.get("price", {}).get("value")
                            if price_val:
                                ebay_listings.append((item.get("title",""), price_val, item.get("itemWebUrl","")))
                    except:
                        pass

                    if ebay_listings:
                        st.subheader("eBay Live Listings")
                        for t,v,url in ebay_listings:
                            st.markdown(f"[{t}]({url}) — £{v}")
                    else:
                        st.info("No eBay listings found.")
                else:
                    st.warning("Could not authenticate with eBay.")

