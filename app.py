import math
import time
import html
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st

# ------------------------------------------------------------
# Page setup
# ------------------------------------------------------------
st.set_page_config(page_title="Pokémon Sold Price Finder", layout="wide")

# ------------------------------------------------------------
# Secrets / Config
# ------------------------------------------------------------
EBAY_APP_ID = (
    st.secrets.get("EBAY_APP_ID")
    or st.secrets.get("ebay_app_id")
    or st.secrets.get("EBAY_APPID")
)

# Optional but recommended to avoid low limits
POKEMONTCG_API_KEY = st.secrets.get("POKEMONTCG_API_KEY", None)

POKEMONTCG_ENDPOINT = "https://api.pokemontcg.io/v2/cards"
FINDING_ENDPOINT = "https://svcs.ebay.com/services/search/FindingService/v1"

# Default to UK. You can switch to EBAY-US etc.
EBAY_GLOBAL_ID = "EBAY-GB"


# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------
def money_fmt(value, currency="GBP"):
    if value is None:
        return "—"
    try:
        if isinstance(value, float) and math.isnan(value):
            return "—"
    except Exception:
        pass
    return f"{currency} {value:,.2f}"


def safe_snippet(text, n=800):
    if text is None:
        return ""
    text = str(text)
    return text[:n] + ("…" if len(text) > n else "")


def pick_tcgplayer_market(card: dict):
    """
    Returns (market_price, finish_name) if available in the Pokémon TCG API payload.
    """
    tcg = card.get("tcgplayer") or {}
    prices = tcg.get("prices") or {}
    for finish, pdata in prices.items():
        if isinstance(pdata, dict) and pdata.get("market") is not None:
            return float(pdata["market"]), finish
    return None, None


def card_tile_html(image_url: str, name: str, subtitle: str, badge_text: str):
    """
    Creates a tile similar to pokemonpricetracker style:
    - image
    - badge overlay top-right
    - name & subtitle below
    """
    safe_name = html.escape(name or "")
    safe_sub = html.escape(subtitle or "")
    safe_badge = html.escape(badge_text or "")

    img_tag = f'<img src="{html.escape(image_url)}" style="width:100%; display:block;">' if image_url else \
              '<div style="height: 310px; display:flex; align-items:center; justify-content:center; color:#aaa;">No image</div>'

    return f"""
    <div style="width: 100%; max-width: 220px; margin: 0 auto;">
      <div style="
        position: relative;
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid rgba(255,255,255,0.08);
        box-shadow: 0 6px 18px rgba(0,0,0,0.25);
        background: #111;
      ">
        {img_tag}
        <div style="
          position: absolute;
          top: 10px;
          right: 10px;
          background: rgba(0,0,0,0.72);
          color: white;
          padding: 6px 10px;
          border-radius: 10px;
          font-weight: 700;
          font-size: 13px;
          backdrop-filter: blur(6px);
        ">{safe_badge}</div>
      </div>
      <div style="padding: 8px 2px 0 2px;">
        <div style="font-weight: 700; line-height: 1.2;">{safe_name}</div>
        <div style="opacity: 0.75; font-size: 13px; line-height: 1.2;">{safe_sub}</div>
      </div>
    </div>
    """


# ------------------------------------------------------------
# Pokémon TCG API (safe call + caching)
# ------------------------------------------------------------
@st.cache_data(ttl=60 * 30)  # 30 minutes
def pokemontcg_search_cards(query: str, page_size: int = 24):
    """
    Safe wrapper around Pokémon TCG API.
    Returns dict: {ok, status, error, details, data}
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "pokemon-search/1.0",
    }
    if POKEMONTCG_API_KEY:
        headers["X-Api-Key"] = POKEMONTCG_API_KEY

    # Lucene-like query syntax, e.g. name:"charizard"
    params = {
        "q": f'name:"{query}"',
        "page": 1,
        "pageSize": page_size,
        # Keep payload smaller; if you run into 400s, remove 'select' temporarily.
        "select": "id,name,number,set,images,rarity,tcgplayer",
        "orderBy": "set.releaseDate",
    }

    try:
        r = requests.get(POKEMONTCG_ENDPOINT, headers=headers, params=params, timeout=30)
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "error": "Network error calling Pokémon TCG API",
            "details": str(e),
            "data": [],
        }

    if r.status_code == 429:
        return {
            "ok": False,
            "status": 429,
            "error": "Pokémon TCG API rate limit hit (HTTP 429).",
            "details": safe_snippet(r.text, 800),
            "data": [],
        }

    if r.status_code >= 400:
        return {
            "ok": False,
            "status": r.status_code,
            "error": f"Pokémon TCG API error (HTTP {r.status_code}).",
            "details": safe_snippet(r.text, 800),
            "data": [],
        }

    try:
        payload = r.json()
    except Exception as e:
        return {
            "ok": False,
            "status": r.status_code,
            "error": "Failed to parse Pokémon TCG API JSON response.",
            "details": str(e),
            "data": [],
        }

    return {"ok": True, "status": 200, "error": None, "details": None, "data": payload.get("data", [])}


# ------------------------------------------------------------
# eBay Finding API (sold listings) - may be restricted
# ------------------------------------------------------------
def build_ebay_keywords(card: dict):
    """
    Basic keyword builder. You will refine this over time for better matching.
    """
    name = (card.get("name") or "").strip()
    number = (card.get("number") or "").strip()
    set_name = ((card.get("set") or {}).get("name") or "").strip()

    excluded = "-lot -bundle -collection -proxy -digital -code"

    # Use "name" + "set" + "number" to reduce noise
    # (Many sets reuse numbers; this isn't perfect.)
    kw = f'"{name}" "{set_name}" "{number}" pokemon card {excluded}'
    return kw


@st.cache_data(ttl=60 * 20)  # 20 minutes
def ebay_find_completed_items(keywords: str, days: int = 30, entries: int = 50, include_shipping: bool = True):
    """
    Attempts to use eBay Finding API findCompletedItems.
    NOTE: Many developers are blocked/restricted for completed-items access.
    Returns {ok, error, items}
    """
    if not EBAY_APP_ID:
        return {"ok": False, "error": "Missing EBAY_APP_ID in Streamlit secrets.", "items": []}

    end_to = datetime.now(timezone.utc)
    end_from = end_to - timedelta(days=days)

    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "GLOBAL-ID": EBAY_GLOBAL_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": keywords,
        "paginationInput.entriesPerPage": str(entries),
        "sortOrder": "EndTimeSoonest",
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "EndTimeFrom",
        "itemFilter(1).value": end_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "itemFilter(2).name": "EndTimeTo",
        "itemFilter(2).value": end_to.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }

    try:
        r = requests.get(FINDING_ENDPOINT, params=params, timeout=30)
        # Do NOT raise_for_status; we want to show a clean error.
        payload = r.json()
    except Exception as e:
        return {"ok": False, "error": f"Error calling eBay Finding API: {e}", "items": []}

    resp = payload.get("findCompletedItemsResponse", [])
    if not resp:
        return {"ok": False, "error": f"Unexpected eBay response: {safe_snippet(payload, 600)}", "items": []}

    ack = (resp[0].get("ack") or [""])[0]
    if ack != "Success":
        # Often Security / restricted
        return {"ok": False, "error": f"eBay ack={ack}: {safe_snippet(resp[0].get('errorMessage', {}), 600)}", "items": []}

    search_result = resp[0].get("searchResult", [{}])[0]
    items = search_result.get("item", []) or []

    normalized = []
    for it in items:
        title = (it.get("title") or [""])[0]
        url = (it.get("viewItemURL") or [""])[0]
        end_time = ((it.get("listingInfo") or [{}])[0].get("endTime") or [""])[0]

        selling_status = (it.get("sellingStatus") or [{}])[0]
        price_obj = (selling_status.get("currentPrice") or [{}])[0]

        # In JSON, value may appear under "__value__" or "#text"
        raw_price = price_obj.get("__value__", price_obj.get("#text", 0.0))
        try:
            price = float(raw_price or 0.0)
        except Exception:
            price = 0.0

        currency = price_obj.get("@currencyId", price_obj.get("currencyId", "GBP"))

        ship_cost = 0.0
        ship = (it.get("shippingInfo") or [{}])[0]
        ship_cost_obj = ship.get("shippingServiceCost")
        if isinstance(ship_cost_obj, list) and ship_cost_obj:
            ship_cost_obj = ship_cost_obj[0]
            raw_ship = ship_cost_obj.get("__value__", ship_cost_obj.get("#text", 0.0))
            try:
                ship_cost = float(raw_ship or 0.0)
            except Exception:
                ship_cost = 0.0

        total_price = price + ship_cost if include_shipping else price

        normalized.append(
            {
                "title": title,
                "end_time": end_time,
                "price": price,
                "shipping": ship_cost,
                "total_price": total_price,
                "currency": currency,
                "url": url,
            }
        )

    # Filter to most common currency for stats sanity
    if normalized:
        cur = pd.Series([x["currency"] for x in normalized]).mode().iloc[0]
        normalized = [x for x in normalized if x["currency"] == cur]

    return {"ok": True, "error": None, "items": normalized}


def summarize_prices(items):
    if not items:
        return None
    df = pd.DataFrame(items)
    cur = df["currency"].iloc[0] if "currency" in df.columns and len(df) else "GBP"
    return {
        "currency": cur,
        "count": int(len(df)),
        "high": float(df["total_price"].max()),
        "low": float(df["total_price"].min()),
        "avg": float(df["total_price"].mean()),
        "df": df.sort_values("end_time", ascending=False),
    }


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------
st.title("Pokémon Card Sold Prices (Grid + Stats)")

with st.expander("ℹ️ Notes about data sources", expanded=False):
    st.markdown(
        """
- Pokémon card images + set/number come from the Pokémon TCG API. Searching uses the API's Lucene-style `q` parameter (e.g. `name:"Charizard"`).  
- If you **don’t use an API key**, Pokémon TCG API limits are lower (e.g. 30/min and 1000/day), so you should use the **Search** button to avoid calling on every keystroke.  
- eBay **sold-history** is attempted via `findCompletedItems`, but eBay restricted completed-item API access for many developers and moved this capability behind Marketplace Insights access.
"""
    )


# Sidebar controls
st.sidebar.header("Filters")
days = st.sidebar.select_slider("Timescale (days)", options=[7, 14, 30, 60, 90], value=30)
include_shipping = st.sidebar.toggle("Include shipping in price", value=True)
max_cards = st.sidebar.slider("Max card variants to show", 6, 36, 18, step=6)
max_sales = st.sidebar.slider("Max sold listings per card (eBay)", 10, 100, 40, step=10)

# Optional: avoid slow grid by allowing user to toggle eBay lookups
do_ebay = st.sidebar.toggle("Fetch eBay sold data (may be restricted)", value=True)

# Search form (prevents API call on each keystroke)
with st.form("search_form"):
    query = st.text_input("Search for a card (e.g., Charizard, Umbreon VMAX)", value="Charizard")
    submitted = st.form_submit_button("Search")

if not submitted:
    st.stop()

# Call Pokémon TCG API safely
with st.spinner("Searching Pokémon card database…"):
    result = pokemontcg_search_cards(query.strip(), page_size=max_cards)

if not result["ok"]:
    st.error(result["error"])
    st.code(f"HTTP: {result['status']}\n{result['details']}", language="text")

    if result["status"] == 429:
        st.info(
            "You hit the Pokémon TCG API rate limit. Add `POKEMONTCG_API_KEY` in Streamlit secrets "
            "or wait a minute and try again. Using the Search button reduces calls."
        )
    elif result["status"] in (401, 403):
        st.info(
            "This looks like an authentication/permission issue. "
            "Add `POKEMONTCG_API_KEY` to your Streamlit secrets to authenticate via `X-Api-Key`."
        )
    st.stop()

cards = result["data"]
if not cards:
    st.warning("No cards found. Try a different query.")
    st.stop()

st.caption(
    "Grid shows printings across sets. Badge shows eBay HIGH (if available) or TCGplayer market fallback."
)

# Grid
cols_per_row = 6
cols = st.columns(cols_per_row)

# Keep a list of cards for detail view selection
card_labels = []
card_map = {}

for idx, card in enumerate(cards):
    name = card.get("name", "Unknown")
    number = card.get("number", "?")
    set_name = (card.get("set") or {}).get("name", "Unknown set")
    subtitle = f"{number} • {set_name}"
    img = (card.get("images") or {}).get("small") or (card.get("images") or {}).get("large")

    badge_text = "—"
    ebay_summary = None
    ebay_error = None
    keywords = None

    if do_ebay:
        keywords = build_ebay_keywords(card)
        ebay = ebay_find_completed_items(
            keywords,
            days=days,
            entries=max_sales,
            include_shipping=include_shipping,
        )
        if ebay["ok"]:
            ebay_summary = summarize_prices(ebay["items"])
            if ebay_summary:
                badge_text = f"{ebay_summary['currency']} {ebay_summary['high']:.2f}"
            else:
                badge_text = "No sold data"
        else:
            ebay_error = ebay["error"]
            # fallback to tcgplayer market
            market, finish = pick_tcgplayer_market(card)
            if market is not None:
                badge_text = f"TCG Mkt {market:.2f}"
            else:
                badge_text = "Sold blocked"
    else:
        market, finish = pick_tcgplayer_market(card)
        badge_text = f"TCG Mkt {market:.2f}" if market is not None else "—"

    tile = card_tile_html(img, name, subtitle, badge_text)

    with cols[idx % cols_per_row]:
        st.markdown(tile, unsafe_allow_html=True)

    label = f"{name} — {subtitle}"
    card_labels.append(label)
    card_map[label] = {
        "card": card,
        "keywords": keywords,
        "ebay_summary": ebay_summary,
        "ebay_error": ebay_error,
    }

st.divider()

# Detail section
st.header("Details")
selected = st.selectbox("Pick a card printing for details", options=card_labels)

selected_obj = card_map[selected]
card = selected_obj["card"]
keywords = selected_obj["keywords"]
ebay_summary = selected_obj["ebay_summary"]
ebay_error = selected_obj["ebay_error"]

name = card.get("name", "Unknown")
number = card.get("number", "?")
set_name = (card.get("set") or {}).get("name", "Unknown set")
img = (card.get("images") or {}).get("large") or (card.get("images") or {}).get("small")

left, right = st.columns([1, 2])
with left:
    st.subheader(f"{name}")
    st.caption(f"{number} • {set_name}")
    st.image(img, use_container_width=True)

    if keywords:
        st.caption("eBay keywords used")
        st.code(keywords, language="text")

with right:
    if do_ebay and ebay_summary:
        c = ebay_summary["currency"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Highest", money_fmt(ebay_summary["high"], c))
        m2.metric("Average", money_fmt(ebay_summary["avg"], c))
        m3.metric("Lowest", money_fmt(ebay_summary["low"], c))
        m4.metric("Samples", str(ebay_summary["count"]))

        # Fetch full sold items (only for selected card) to show table
        ebay_full = ebay_find_completed_items(
            build_ebay_keywords(card),
            days=days,
            entries=max_sales,
            include_shipping=include_shipping,
        )

        if ebay_full["ok"] and ebay_full["items"]:
            df = pd.DataFrame(ebay_full["items"])
            df["end_time"] = pd.to_datetime(df["end_time"], errors="coerce")
            df = df.sort_values("end_time", ascending=False)
            df["end_time"] = df["end_time"].dt.strftime("%Y-%m-%d %H:%M")
            for col in ["price", "shipping", "total_price"]:
                df[col] = df[col].round(2)

            st.dataframe(
                df[["end_time", "title", "price", "shipping", "total_price", "currency", "url"]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.warning("No sold listings returned for this selection.")
            if not ebay_full["ok"]:
                st.code(ebay_full["error"], language="text")

    elif do_ebay and ebay_error:
        st.error("Could not fetch sold listings from eBay (likely restricted for completed items).")
        st.code(ebay_error, language="text")
        st.info(
            "If you later gain Marketplace Insights access, we can swap the backend to the official sold-history API."
        )

        market, finish = pick_tcgplayer_market(card)
        if market is not None:
            st.metric("TCGplayer Market (fallback)", f"{market:.2f}")
    else:
        market, finish = pick_tcgplayer_market(card)
        if market is not None:
            st.metric("TCGplayer Market", f"{market:.2f}")
        else:
            st.info("No eBay or TCGplayer price data available for this card.")
