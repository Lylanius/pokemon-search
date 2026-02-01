import os
import math
import html
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="Pokémon Sold Price Finder", layout="wide")

# -----------------------------
# Configuration / Secrets
# -----------------------------
EBAY_APP_ID = st.secrets.get("EBAY_APP_ID") or st.secrets.get("ebay_app_id") or st.secrets.get("EBAY_APPID")
POKEMONTCG_API_KEY = st.secrets.get("POKEMONTCG_API_KEY", None)

EBAY_GLOBAL_ID = "EBAY-GB"  # UK site; adjust if you want EBAY-US etc.
FINDING_ENDPOINT = "https://svcs.ebay.com/services/search/FindingService/v1"
POKEMONTCG_ENDPOINT = "https://api.pokemontcg.io/v2/cards"

# -----------------------------
# Helpers
# -----------------------------
def money_fmt(value, currency="GBP"):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{currency} {value:,.2f}"

@st.cache_data(ttl=60 * 30)  # 30 minutes
def pokemontcg_search_cards(query: str, page_size: int = 24):
    headers = {}
    if POKEMONTCG_API_KEY:
        headers["X-Api-Key"] = POKEMONTCG_API_KEY

    # Use Lucene-like query syntax: name:"..."
    q = f'name:"{query}"'
    params = {
        "q": q,
        "page": 1,
        "pageSize": page_size,
        # Only bring back fields we need to keep payload smaller
        "select": "id,name,number,set,images,rarity,tcgplayer",
        "orderBy": "set.releaseDate"
    }
    r = requests.get(POKEMONTCG_ENDPOINT, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", [])
    return data

def build_ebay_keywords(card):
    """
    Keywords tuned to reduce junk. You can refine this heavily over time.
    """
    name = card.get("name", "")
    number = card.get("number", "")
    set_name = (card.get("set") or {}).get("name", "")
    rarity = card.get("rarity", "")

    # Example search phrase:
    # "Charizard" "Base Set" "4/102" Pokémon card
    # (number can be e.g. "4" so we include just number and optionally total if known)
    total = (card.get("set") or {}).get("printedTotal") or (card.get("set") or {}).get("total")
    num_str = f"{number}/{total}" if total else str(number)

    # Exclusions help remove bulk lots
    excluded = "-lot -bundle -collection -proxy -digital -code"
    keywords = f'"{name}" "{set_name}" "{num_str}" pokemon card {excluded}'
    # If no total, omit 4/102 pattern
    if not total:
        keywords = f'"{name}" "{set_name}" "{number}" pokemon card {excluded}'
    return keywords

@st.cache_data(ttl=60 * 20)  # 20 minutes
def ebay_find_completed_items(keywords: str, days: int = 30, entries: int = 50, include_shipping: bool = True):
    """
    Attempts to use Finding API findCompletedItems.
    NOTE: This endpoint is often restricted for many apps.
    """
    if not EBAY_APP_ID:
        return {"ok": False, "error": "Missing EBAY_APP_ID in Streamlit secrets.", "items": []}

    end_to = datetime.now(timezone.utc)
    end_from = end_to - timedelta(days=days)

    # Finding API uses itemFilter with names like EndTimeFrom/EndTimeTo.  [9](https://developer.ebay.com/devzone//finding/callref/extra/fnditmsadvncd.rqst.tmfltr.nm.html)
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
        r.raise_for_status()
        payload = r.json()

        resp = payload.get("findCompletedItemsResponse", [])
        if not resp:
            return {"ok": False, "error": "Unexpected response shape from eBay Finding API.", "items": []}

        ack = (resp[0].get("ack") or [""])[0]
        if ack != "Success":
            # Many apps get Security errors / call restricted.
            err = resp[0].get("errorMessage", {})
            return {"ok": False, "error": f"eBay ack={ack}. error={err}", "items": []}

        search_result = resp[0].get("searchResult", [{}])[0]
        items = search_result.get("item", []) or []

        normalized = []
        for it in items:
            title = (it.get("title") or [""])[0]
            url = (it.get("viewItemURL") or [""])[0]
            end_time = ((it.get("listingInfo") or [{}])[0].get("endTime") or [""])[0]

            selling_status = (it.get("sellingStatus") or [{}])[0]
            price_obj = (selling_status.get("currentPrice") or [{}])[0]
            price = float(price_obj.get("__value__", price_obj.get("#text", 0.0)) or 0.0)
            currency = price_obj.get("@currencyId", price_obj.get("currencyId", "GBP"))

            ship_cost = 0.0
            ship = (it.get("shippingInfo") or [{}])[0]
            ship_cost_obj = (ship.get("shippingServiceCost") or [{}])
            if ship_cost_obj:
                ship_cost_obj = ship_cost_obj[0]
                try:
                    ship_cost = float(ship_cost_obj.get("__value__", ship_cost_obj.get("#text", 0.0)) or 0.0)
                except Exception:
                    ship_cost = 0.0

            total_price = price + ship_cost if include_shipping else price

            normalized.append({
                "title": title,
                "end_time": end_time,
                "price": price,
                "shipping": ship_cost,
                "total_price": total_price,
                "currency": currency,
                "url": url
            })

        # Filter to a single currency for sane stats
        if normalized:
            # choose the most common currency
            cur = pd.Series([x["currency"] for x in normalized]).mode().iloc[0]
            normalized = [x for x in normalized if x["currency"] == cur]
        return {"ok": True, "error": None, "items": normalized}

    except requests.HTTPError as e:
        return {"ok": False, "error": f"HTTP error from eBay: {e}", "items": []}
    except Exception as e:
        return {"ok": False, "error": f"Error calling eBay: {e}", "items": []}

def summarize_prices(items):
    if not items:
        return None
    df = pd.DataFrame(items)
    cur = df["currency"].iloc[0] if "currency" in df.columns and len(df) else "GBP"
    return {
        "currency": cur,
        "count": len(df),
        "high": float(df["total_price"].max()),
        "low": float(df["total_price"].min()),
        "avg": float(df["total_price"].mean()),
        "df": df.sort_values("end_time", ascending=False)
    }

def card_tile_html(img_url, name, subtitle, price_text):
    """
    Creates a tile similar to pokemonpricetracker style:
    image, price overlay top-right, name and subtitle under.
    """
    safe_name = html.escape(name)
    safe_sub = html.escape(subtitle)
    safe_price = html.escape(price_text)

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
        <img src="{img_url}" style="display:block; width: 100%; height:auto;" />
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
        ">{safe_price}</div>
      </div>
      <div style="padding: 8px 2px 0 2px;">
        <div style="font-weight: 700; line-height: 1.2;">{safe_name}</div>
        <div style="opacity: 0.75; font-size: 13px; line-height: 1.2;">{safe_sub}</div>
      </div>
    </div>
    """

# -----------------------------
# Sidebar controls
# -----------------------------
st.sidebar.header("Filters")
days = st.sidebar.select_slider("Timescale (days)", options=[7, 14, 30, 60, 90], value=30)
include_shipping = st.sidebar.toggle("Include shipping in price", value=True)
max_cards = st.sidebar.slider("Max card variants to show", 6, 36, 18, step=6)
max_sales = st.sidebar.slider("Max sold listings per card (eBay)", 10, 100, 40, step=10)

st.title("Pokémon Card Sold Prices (eBay) + Card Grid")

query = st.text_input("Search for a card (e.g., Charizard, Pikachu VMAX, Umbreon)", value="Charizard")

if query:
    with st.spinner("Searching Pokémon card database…"):
        cards = pokemontcg_search_cards(query, page_size=max_cards)

    if not cards:
        st.warning("No cards found. Try a different query.")
        st.stop()

    st.caption("Tip: Searches return multiple printings across sets. The grid shows each printing and its sold-price summary.")

    # Build grid
    cols_per_row = 6 if st.session_state.get("wide", True) else 4
    cols = st.columns(cols_per_row)

    # We'll also collect expanded details below
    detail_sections = []

    for idx, card in enumerate(cards):
        col = cols[idx % cols_per_row]

        img = (card.get("images") or {}).get("small") or (card.get("images") or {}).get("large")
        name = card.get("name", "Unknown")
        number = card.get("number", "?")
        set_name = (card.get("set") or {}).get("name", "Unknown set")

        subtitle = f"{number} • {set_name}"

        keywords = build_ebay_keywords(card)
        ebay = ebay_find_completed_items(keywords, days=days, entries=max_sales, include_shipping=include_shipping)
        summary = summarize_prices(ebay["items"]) if ebay["ok"] else None

        if summary:
            price_text = f"{summary['currency']} {summary['high']:.2f}"
        else:
            # fallback to tcgplayer market if present
            tcgplayer = card.get("tcgplayer", {}) or {}
            prices = (tcgplayer.get("prices") or {})
            # pick first finish available
            market = None
            for finish, p in prices.items():
                if isinstance(p, dict) and p.get("market") is not None:
                    market = p.get("market")
                    break
            if market is not None:
                price_text = f"TCG Mkt {market:.2f}"
            else:
                price_text = "No sold data"

        tile = card_tile_html(img, name, subtitle, price_text)
        with col:
            st.markdown(tile, unsafe_allow_html=True)

            # Expand button
            show = st.button(f"Details #{idx+1}", key=f"btn_{card.get('id')}_{idx}")
            if show:
                detail_sections.append((card, ebay, summary, keywords))

    # Detailed panels
    if detail_sections:
        st.divider()
        st.header("Details")

        for card, ebay, summary, keywords in detail_sections:
            name = card.get("name", "Unknown")
            number = card.get("number", "?")
            set_name = (card.get("set") or {}).get("name", "Unknown set")
            img = (card.get("images") or {}).get("large") or (card.get("images") or {}).get("small")

            st.subheader(f"{name} — {number} • {set_name}")
            left, right = st.columns([1, 2])
            with left:
                st.image(img, use_container_width=True)
                st.code(keywords, language="text")

            with right:
                if ebay["ok"] and summary:
                    c = summary["currency"]
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Highest", money_fmt(summary["high"], c))
                    m2.metric("Average", money_fmt(summary["avg"], c))
                    m3.metric("Lowest", money_fmt(summary["low"], c))
                    m4.metric("Samples", f"{summary['count']}")

                    df = summary["df"].copy()
                    df["end_time"] = pd.to_datetime(df["end_time"], errors="coerce")
                    df["end_time"] = df["end_time"].dt.strftime("%Y-%m-%d %H:%M")
                    df["total_price"] = df["total_price"].round(2)
                    df["shipping"] = df["shipping"].round(2)
                    df["price"] = df["price"].round(2)

                    st.dataframe(
                        df[["end_time", "title", "price", "shipping", "total_price", "currency", "url"]],
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.error(
                        "Could not fetch sold listings from eBay (likely restricted). "
                        "If you want official sold-history via API, you’ll need Marketplace Insights access."
                    )
                    st.caption(f"Raw error: {ebay.get('error')}")
                    # show tcgplayer fallback if present
                    tcgplayer = card.get("tcgplayer", {}) or {}
                    if tcgplayer:
                        st.write("TCGplayer pricing (fallback from Pokémon TCG API dataset):")
                        st.json(tcgplayer.get("prices", {}))
