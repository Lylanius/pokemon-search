import math
import html
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

# Optional (recommended) PokémonTCG key to improve rate limits
POKEMONTCG_API_KEY = st.secrets.get("POKEMONTCG_API_KEY", None)

# Primary endpoints
POKEMONTCG_ENDPOINT = "https://api.pokemontcg.io/v2/cards"
FINDING_ENDPOINT = "https://svcs.ebay.com/services/search/FindingService/v1"

# Fallback provider (TCGdex)
TCGDEX_LIST_ENDPOINT = "https://api.tcgdex.net/v2/en/cards"
TCGDEX_CARD_ENDPOINT = "https://api.tcgdex.net/v2/en/cards/{card_id}"

# Default to UK eBay site
EBAY_GLOBAL_ID = "EBAY-GB"

# ------------------------------------------------------------
# HTTP session with retries/backoff
# ------------------------------------------------------------
@st.cache_resource
def http_session():
    session = requests.Session()

    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# ------------------------------------------------------------
# Utility helpers
# ------------------------------------------------------------
def safe_snippet(text, n=800):
    if text is None:
        return ""
    text = str(text)
    return text[:n] + ("…" if len(text) > n else "")

def money_fmt(value, currency="GBP"):
    if value is None:
        return "—"
    try:
        if isinstance(value, float) and math.isnan(value):
            return "—"
    except Exception:
        pass
    return f"{currency} {value:,.2f}"

# ------------------------------------------------------------
# ✅ TCGdex image URL reconstruction helper
# ------------------------------------------------------------
def tcgdex_card_image(base_url: str, quality="low", ext="webp") -> str | None:
    """
    TCGdex often returns an image URL WITHOUT extension. That's normal.
    To get the actual file, append /{quality}.{extension}
    e.g. https://assets.tcgdex.net/en/swsh/swsh3/136/low.webp [1](https://developer.ebay.com/support/kb-article?KBid=1445)
    """
    if not base_url:
        return None

    # Already a file URL? keep it
    if base_url.endswith((".png", ".webp", ".jpg", ".jpeg")):
        return base_url

    return f"{base_url}/{quality}.{ext}"

# ------------------------------------------------------------
# ✅ Tile HTML: MUST render an <img>, not the URL text
# ------------------------------------------------------------
def card_tile_html(image_url: str, name: str, subtitle: str, badge_text: str):
    safe_name = html.escape(name or "")
    safe_sub = html.escape(subtitle or "")
    safe_badge = html.escape(badge_text or "")

    if image_url:
        safe_src = html.escape(image_url, quote=True)
        img_html = f"""
          <img src="{safe_src}" alt="{safe_name}"
               style="width:100%; height:auto; display:block; background:#111;" />
        """
    else:
        img_html = """
        <div style="height:310px; display:flex; align-items:center; justify-content:center; color:#aaa;">
          No image
        </div>
        """

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
        {img_html}
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
# PokémonTCG helpers (primary)
# ------------------------------------------------------------
def pick_pokemontcg_market(card: dict):
    tcg = card.get("tcgplayer") or {}
    prices = tcg.get("prices") or {}
    for finish, pdata in prices.items():
        if isinstance(pdata, dict) and pdata.get("market") is not None:
            return float(pdata["market"]), finish
    return None, None

def normalize_from_pokemontcg(card: dict):
    set_obj = card.get("set") or {}
    images = card.get("images") or {}
    market, _finish = pick_pokemontcg_market(card)

    return {
        "source": "pokemontcg",
        "id": card.get("id"),
        "name": card.get("name", "Unknown"),
        "number": card.get("number", "?"),
        "set_name": set_obj.get("name", "Unknown set"),
        "image_small": images.get("small"),
        "image_large": images.get("large"),
        "market_price": market,
        "raw": card,
    }

@st.cache_data(ttl=60 * 30)
def pokemontcg_search_cards(query: str, page_size: int, timeout=(5, 60)):
    s = http_session()

    headers = {"Accept": "application/json", "User-Agent": "pokemon-search/1.0"}
    if POKEMONTCG_API_KEY:
        headers["X-Api-Key"] = POKEMONTCG_API_KEY

    params = {
        "q": f'name:"{query}"',
        "page": 1,
        "pageSize": page_size,
        "select": "id,name,number,set,images,rarity,tcgplayer",
        "orderBy": "set.releaseDate",
    }

    try:
        r = s.get(POKEMONTCG_ENDPOINT, headers=headers, params=params, timeout=timeout)
    except requests.exceptions.ReadTimeout as e:
        return {"ok": False, "status": None, "error": "PokémonTCG API read timeout", "details": str(e), "data": []}
    except Exception as e:
        return {"ok": False, "status": None, "error": "Network error calling PokémonTCG API", "details": str(e), "data": []}

    if r.status_code == 429:
        return {"ok": False, "status": 429, "error": "PokémonTCG API rate limit hit (HTTP 429).", "details": safe_snippet(r.text), "data": []}
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": f"PokémonTCG API error (HTTP {r.status_code}).", "details": safe_snippet(r.text), "data": []}

    payload = r.json()
    return {"ok": True, "status": 200, "error": None, "details": None, "data": payload.get("data", [])}

# ------------------------------------------------------------
# TCGdex helpers (fallback)
# ------------------------------------------------------------
def normalize_from_tcgdex(card: dict):
    """
    TCGdex 'image' is optional (can be missing). [2](https://developer.ebay.com/Devzone/finding/CallRef/Samples/findItemsByCategory_aspectHist_out_json.txt)
    When it exists, it may have no extension; we reconstruct with /low.webp and /high.webp. [1](https://developer.ebay.com/support/kb-article?KBid=1445)
    """
    set_obj = card.get("set") or {}
    base_image = card.get("image")  # often extensionless

    image_small = tcgdex_card_image(base_image, quality="low", ext="webp")
    image_large = tcgdex_card_image(base_image, quality="high", ext="webp")

    # Optional: best-effort pricing (leave None for now; can be improved)
    return {
        "source": "tcgdex",
        "id": card.get("id"),
        "name": card.get("name", "Unknown"),
        "number": card.get("localId", "?"),
        "set_name": set_obj.get("name", "Unknown set"),
        "image_small": image_small,
        "image_large": image_large,
        "market_price": None,
        "raw": card,
    }

@st.cache_data(ttl=60 * 30)
def tcgdex_search_cards(query: str, page_size: int, timeout=(5, 60)):
    s = http_session()
    params = {"name": query}  # lax contains   r = s.get(TCGDEX_LIST_ENDPOINT, params=params, timeout=timeout)
    except requests.exceptions.ReadTimeout as e:
        return {"ok": False, "status": None, "error": "TCGdex API read timeout", "details": str(e), "data": []}
    except Exception as e:
        return {"ok": False, "status": None, "error": "Network error calling TCGdex API", "details": str(e), "data": []}

    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": f"TCGdex API error (HTTP {r.status_code}).", "details": safe_snippet(r.text), "data": []}

    data = r.json()  # list endpoint returns an array
    return {"ok": True, "status": 200, "error": None, "details": None, "data": data[:page_size]}

@st.cache_data(ttl=60 * 60)
def tcgdex_get_card(card_id: str, timeout=(5, 60)):
    s = http_session()
    url = TCGDEX_CARD_ENDPOINT.format(card_id=card_id)
    r = s.get(url, timeout=timeout)
    if r.status_code >= 400:
        return None
    return r.json()

# ------------------------------------------------------------
# eBay sold listings (may be restricted)
# ------------------------------------------------------------
def build_ebay_keywords(norm_card: dict):
    name = (norm_card.get("name") or "").strip()
    number = (norm_card.get("number") or "").strip()
    set_name = (norm_card.get("set_name") or "").strip()
    excluded = "-lot -bundle -collection -proxy -digital -code"
    return f'"{name}" "{set_name}" "{number}" pokemon card {excluded}'

@st.cache_data(ttl=60 * 20)
def ebay_find_completed_items(keywords: str, days: int = 30, entries: int = 50, include_shipping: bool = True):
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
        r = requests.get(FINDING_ENDPOINT, params=params, timeout=(5, 30))
        payload = r.json()
    except Exception as e:
        return {"ok": False, "error": f"Error calling eBay Finding API: {e}", "items": []}

    resp = payload.get("findCompletedItemsResponse", [])
    if not resp:
        return {"ok": False, "error": f"Unexpected eBay response: {safe_snippet(payload, 600)}", "items": []}

    ack = (resp[0].get("ack") or [""])[0]
    if ack != "Success":
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
- If we fall back to **TCGdex**, card images must be rendered by appending `/{quality}.{ext}` (e.g. `/low.webp`). [1](https://developer.ebay.com/support/kb-article?KBid=1445)  
- Some **TCGdex** entries may have no `image` field at all (it’s optional), in which case “No image” is expected. [2](https://developer.ebay.com/Devzone/finding/CallRef/Samples/findItemsByCategory_aspectHist_out_json.txt)
        """
    )

st.sidebar.header("Filters")
days = st.sidebar.select_slider("Timescale (days)", options=[7, 14, 30, 60, 90], value=30)
include_shipping = st.sidebar.toggle("Include shipping in price", value=True)
max_cards = st.sidebar.slider("Max card variants to show", 6, 36, 18, step=6)
max_sales = st.sidebar.slider("Max sold listings per card (eBay)", 10, 100, 40, step=10)
do_ebay = st.sidebar.toggle("Fetch eBay sold data (may be restricted)", value=True)

connect_timeout = st.sidebar.slider("API connect timeout (sec)", 2, 20, 5)
read_timeout = st.sidebar.slider("API read timeout (sec)", 10, 120, 60)
timeout_tuple = (connect_timeout, read_timeout)

with st.form("search_form"):
    query = st.text_input("Search for a card (e.g., Charizard, Umbreon VMAX)", value="Charizard")
    submitted = st.form_submit_button("Search")

if not submitted:
    st.stop()

query = query.strip()
if not query:
    st.warning("Enter a search term.")
    st.stop()

# Try PokémonTCG first
with st.spinner("Searching Pokémon card database…"):
    primary = pokemontcg_search_cards(query, page_size=max_cards, timeout=timeout_tuple)

normalized_cards = []
provider_used = None

if primary["ok"]:
    provider_used = "PokémonTCG"
    normalized_cards = [normalize_from_pokemontcg(c) for c in primary["data"]]
else:
    with st.spinner("PokémonTCG unavailable — falling back to TCGdex…"):
        fallback = tcgdex_search_cards(query, page_size=max_cards, timeout=timeout_tuple)

    if fallback["ok"] and fallback["data"]:
        provider_used = "TCGdex"
        full_cards = []
        for brief in fallback["data"]:
            cid = brief.get("id")
            full = tcgdex_get_card(cid, timeout=timeout_tuple)
            if full:
                full_cards.append(full)
        normalized_cards = [normalize_from_tcgdex(c) for c in full_cards]
    else:
        st.error("Both PokémonTCG and TCGdex look unavailable right now.")
        st.markdown("### PokémonTCG error")
        st.code(f"HTTP: {primary.get('status')}\n{primary.get('details')}", language="text")
        st.markdown("### TCGdex error")
        st.code(
            f"HTTP: {fallback.get('status') if 'fallback' in locals() else None}\n"
            f"{fallback.get('details') if 'fallback' in locals() else ''}",
            language="text",
        )
        st.stop()

if not normalized_cards:
    st.warning("No cards found. Try a different query.")
    st.stop()

st.caption(f"Card data provider used: **{provider_used}**")

# Grid
cols_per_row = 6
cols = st.columns(cols_per_row)

card_labels = []
card_map = {}

for idx, card in enumerate(normalized_cards):
    name = card["name"]
    number = card["number"]
    set_name = card["set_name"]
    subtitle = f"{number} • {set_name}"
    img = card["image_small"] or card["image_large"]

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
            badge_text = f"{ebay_summary['currency']} {ebay_summary['high']:.2f}" if ebay_summary else "No sold data"
        else:
            ebay_error = ebay["error"]
            badge_text = "Sold blocked"
    else:
        badge_text = "—"

    tile = card_tile_html(img, name, subtitle, badge_text)
    with cols[idx % cols_per_row]:
        st.markdown(tile, unsafe_allow_html=True)

    label = f"{name} — {subtitle}"
    card_labels.append(label)
    card_map[label] = {"card": card, "keywords": keywords, "ebay_summary": ebay_summary, "ebay_error": ebay_error}

st.divider()

# Details
st.header("Details")
selected = st.selectbox("Pick a card printing for details", options=card_labels)

selected_obj = card_map[selected]
card = selected_obj["card"]
keywords = selected_obj["keywords"]
ebay_summary = selected_obj["ebay_summary"]
ebay_error = selected_obj["ebay_error"]

left, right = st.columns([1, 2])
with left:
    st.subheader(card["name"])
    st.caption(f"{card['number']} • {card['set_name']}")
    st.image(card["image_large"] or card["image_small"], use_container_width=True)
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
    else:
        st.info("eBay sold data is disabled, or no data available.")
