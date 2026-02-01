# app.py
import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import numpy as np
import base64
from urllib.parse import urljoin, urlparse

# ==========================
# CONFIG / DEBUG
# ==========================
DEBUG_API = False  # set True temporarily to see request/redirect details

st.set_page_config(page_title="Poké‑Quant Master", layout="wide")

# ==========================
# SECRETS & SANITY CHECKS
# ==========================
def _fatal(msg: str):
    st.error(msg)
    st.stop()

required = ["POKEMON_API_KEY", "EBAY_APP_ID", "EBAY_CERT_ID"]
missing = [k for k in required if k not in st.secrets]
if missing:
    _fatal(f"Missing secrets: {missing}. "
           "Add them in .streamlit/secrets.toml (local) or the Streamlit Cloud Secrets UI, then restart.")

POKEMON_API_KEY = (st.secrets["POKEMON_API_KEY"] or "").strip()
EBAY_APP_ID     = (st.secrets["EBAY_APP_ID"] or "").strip()
EBAY_CERT_ID    = (st.secrets["EBAY_CERT_ID"] or "").strip()

if not POKEMON_API_KEY:
    _fatal("POKEMON_API_KEY is empty/whitespace. Double-check the secret value and restart the app.")
if not EBAY_APP_ID or not EBAY_CERT_ID:
    _fatal("EBAY_APP_ID / EBAY_CERT_ID are empty. Please set them and restart the app.")

DEFAULT_COMPS_LIMIT = 20

# ==========================
# HELPERS
# ==========================
def _auth_header():
    """Pokémon API requires 'Authorization: Bearer <KEY>' exactly as per their error message."""
    return {
        "Authorization": f"Bearer {POKEMON_API_KEY}",
        "Accept": "application/json",
    }

def _req_follow_auth(method, url, *, headers=None, params=None, data=None, json=None,
                     max_redirects=3, timeout=30):
    """
    Make an HTTP request that:
      - Sends Authorization (Bearer) header
      - Follows redirects manually and re-attaches Authorization even if host changes.
    This avoids the common 'Authorization header missing' error after redirects.
    """
    # Ensure Authorization + sensible defaults
    headers = {**(headers or {})}
    # Attach Authorization if not already present
    if "authorization" not in {k.lower(): k for k in headers}.keys():
        headers.update(_auth_header())

    current_url = url
    for hop in range(max_redirects + 1):
        r = requests.request(
            method,
            current_url,
            headers=headers,
            params=params,
            data=data,
            json=json,
            timeout=timeout,
            allow_redirects=False,
        )

        if DEBUG_API:
            st.info(
                f"[{r.status_code}] {method} {current_url}\n"
                f"Sent headers (subset): {{'Authorization': '...redacted...', 'Accept': '{headers.get('Accept','')}'}}"
            )

        # Not a redirect: return
        if r.status_code not in (301, 302, 303, 307, 308):
            if r.status_code >= 400 and DEBUG_API:
                # Show only first 1000 chars to avoid flooding the UI
                st.error(f"HTTP {r.status_code} {current_url}\n{r.text[:1000]}")
            r.raise_for_status()
            return r

        # Handle redirect
        location = r.headers.get("Location")
        if not location:
            r.raise_for_status()  # No location? Treat as error

        # Resolve relative redirects
        parsed = urlparse(location)
        if not parsed.scheme:
            current_url = urljoin(current_url, location)
        else:
            current_url = location

        # Loop continues with the same headers (Authorization preserved)

    raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects while requesting {url}")

def parse_iso(dt_str):
    if not dt_str:
        return None
    s = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def price_link(label, price):
    try:
        p = float(price)
        return f"{label}: £{p:.2f}"
    except Exception:
        return f"{label}: —"

# ==========================
# POKÉMON PRICE TRACKER API
# ==========================
# If the site redirects between apex <-> www, our helper above will handle it safely.
API_ROOT = "https://www.pokemonpricetracker.com/api/v2"

def search_card(query: str):
    """Search cards by name (keep it simple; API may not like complex queries)."""
    if not query.strip():
        return []
    params = {"q": query.strip()}  # If this ever 400s with msg about param name, try 'name' instead.
    r = _req_follow_auth("GET", f"{API_ROOT}/cards", params=params)
    payload = r.json()
    return payload.get("cards") or payload.get("data") or []

def get_card_price(card_id: str):
    r = _req_follow_auth("GET", f"{API_ROOT}/cards/{card_id}")
    return r.json()

def get_price_history(card_id: str, days: int = 30, limit: int = 20):
    params = {"days": int(days), "limit": int(limit)}
    r = _req_follow_auth("GET", f"{API_ROOT}/cards/{card_id}/history", params=params)
    data = r.json()
    if isinstance(data, dict) and "history" in data:
        data = data["history"]
    return (data or [])[:limit]

# ==========================
# EBAY BROWSE API
# ==========================
def get_ebay_token():
    url = "https://api.ebay.com/identity/v1/oauth2/token"
    encoded = base64.b64encode(f"{EBAY_APP_ID}:{EBAY_CERT_ID}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded}",
        "Accept": "application/json",
    }
    # IMPORTANT: Include Browse scope
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/buy.browse.readonly"
    }
    r = requests.post(url, headers=headers, data=data, timeout=30)
    if r.status_code >= 400 and DEBUG_API:
        st.error(f"eBay token error [{r.status_code}]: {r.text[:1000]}")
    r.raise_for_status()
    return r.json().get("access_token")

def get_ebay_live_listings(keywords: str, limit: int = 10, uk_only: bool = True):
    token = get_ebay_token()
    if not token:
        return []
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB" if uk_only else "EBAY_US",
        "Accept": "application/json",
    }
    params = {"q": keywords, "limit": limit}
    if uk_only:
        params["filter"] = "itemLocationCountry:GB"

    r = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers=headers, params=params, timeout=30
    )
    if r.status_code >= 400 and DEBUG_API:
        st.error(f"eBay browse error [{r.status_code}]: {r.text[:1000]}")
    r.raise_for_status()

    items = []
    j = r.json() if r.text else {}
    for i in j.get("itemSummaries", []):
        try:
            price = float(i["price"]["value"])
        except Exception:
            continue
        items.append({
            "title": i.get("title", ""),
            "price": price,
            "url":   i.get("itemWebUrl") or i.get("itemAffiliateWebUrl", "")
        })
    return items

# ==========================
# STREAMLIT UI
# ==========================
st.title("Poké‑Quant Master 🔍")

# Sidebar controls
with st.sidebar:
    search_query = st.text_input("Card Name", value="Pikachu")
    comps_limit  = st.slider("Number of Sold Comps", 3, 50, value=DEFAULT_COMPS_LIMIT)
    days_window  = st.select_slider("Search Window (Days)", options=[30, 60, 90], value=90)
    search_button = st.button("SEARCH CARD")

# Persist minimal state so selection survives reruns
if "cards" not in st.session_state:
    st.session_state.cards = []
if "selected_card_idx" not in st.session_state:
    st.session_state.selected_card_idx = 0

# If user clicked search, call Pokémon search
if search_button:
    try:
        cards = search_card(search_query)
        st.session_state.cards = cards or []
        st.session_state.selected_card_idx = 0
        if not cards:
            st.warning("No cards found. Try a simpler search (just the card name).")
    except requests.exceptions.HTTPError as e:
        st.error(f"Pokémon API HTTP error: {e}")
    except Exception as e:
        st.error(f"Unexpected Pokémon API error: {e}")

# If we have cards in state, show selection + details
if st.session_state.cards:
    cards = st.session_state.cards
    card_names = [c.get("name", "Unknown") for c in cards]

    # Show selectbox, persist index
    selected_name = st.selectbox(
        "Select Card",
        options=card_names,
        index=st.session_state.selected_card_idx if 0 <= st.session_state.selected_card_idx < len(card_names) else 0,
        key="select_card_box"
    )
    st.session_state.selected_card_idx = card_names.index(selected_name)
    card_id = cards[st.session_state.selected_card_idx].get("id")

    # Pull details
    try:
        card_data = get_card_price(card_id)
    except Exception as e:
        st.error(f"Failed to fetch card details: {e}")
        card_data = {}

    try:
        sold_history = get_price_history(card_id, days=days_window, limit=comps_limit)
    except Exception as e:
        st.error(f"Failed to fetch sold history: {e}")
        sold_history = []

    # Render card info
    st.subheader("Card Info")
    st.write(f"**Name:** {card_data.get('name', selected_name)}")
    st.write(price_link("Market Price", card_data.get("market_price")))
    st.write(price_link("Low Price", card_data.get("low_price")))
    st.write(price_link("High Price", card_data.get("high_price")))

    # Render sold history (table + plot)
    if sold_history:
        # Parse and clean dates/prices
        dates_raw = [parse_iso(h.get("date") or h.get("sold_at") or h.get("timestamp")) for h in sold_history]
        prices_raw = []
        for h in sold_history:
            val = h.get("price") or h.get("sold_price") or 0
            try:
                prices_raw.append(float(val))
            except Exception:
                prices_raw.append(np.nan)

        # Filter out invalid points
        points = [(d, p) for d, p in zip(dates_raw, prices_raw) if d is not None and np.isfinite(p)]
        if points:
            dates, prices = zip(*points)
            st.subheader("Sold History")
            df = pd.DataFrame({"Date": [d.strftime("%Y-%m-%d") for d in dates], "Price (£)": prices})
            st.dataframe(df, use_container_width=True)

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.scatter(dates, prices, color='green', label="Sold", alpha=0.7)
            if len(prices) > 1:
                x_nums = np.array([d.timestamp() for d in dates])
                y_vals = np.array(prices, dtype=float)
                x_norm = x_nums - x_nums.mean()
                coeffs = np.polyfit(x_norm, y_vals, 1)
                trend_y = np.polyval(coeffs, x_norm)
                ax.plot(dates, trend_y, color='orange', linestyle='--', label="Trend")
            # Market line if available
            try:
                market_val = float(card_data.get("market_price") or 0)
                if market_val > 0:
                    ax.axhline(market_val, color='cyan', linestyle=':', label="Market")
            except Exception:
                pass
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
            fig.autofmt_xdate(rotation=30, ha='right')
            ax.set_ylabel("Price (£)")
            ax.legend()
            st.pyplot(fig)
        else:
            st.info("No valid sold points to display.")
    else:
        st.info("No sold history available for this card.")

    # Live eBay listings (best-effort; keep separate so Pokémon issues don't mask eBay)
    st.subheader("Live eBay Listings")
    try:
        live_items = get_ebay_live_listings(card_data.get("name") or selected_name, limit=10)
        if live_items:
            for item in live_items:
                st.markdown(f"[{item['title']}]({item['url']}) - £{item['price']:.2f}")
        else:
            st.info("No live listings found.")
    except requests.exceptions.HTTPError as e:
        st.error(f"eBay API HTTP error: {e}")
    except Exception as e:
        st.error(f"Unexpected eBay error: {e}")
else:
    st.caption("Tip: enter a simple card name (e.g., 'Pikachu') and click **SEARCH CARD**.")
