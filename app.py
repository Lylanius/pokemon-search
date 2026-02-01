import math
import html
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =========================
# Config / Secrets
# =========================
st.set_page_config(page_title="Pokémon Sold Price Finder", layout="wide")

EBAY_APP_ID = (
    st.secrets.get("EBAY_APP_ID")
    or st.secrets.get("ebay_app_id")
    or st.secrets.get("EBAY_APPID")
)

POKEMONTCG_API_KEY = st.secrets.get("POKEMONTCG_API_KEY", None)

POKEMONTCG_ENDPOINT = "https://api.pokemontcg.io/v2/cards"
FINDING_ENDPOINT = "https://svcs.ebay.com/services/search/FindingService/v1"

TCGDEX_LIST_ENDPOINT = "https://api.tcgdex.net/v2/en/cards"
TCGDEX_CARD_ENDPOINT = "https://api.tcgdex.net/v2/en/cards/{card_id}"

EBAY_GLOBAL_ID = "EBAY-GB"


# =========================
# Robust HTTP session
# =========================
@st.cache_resource
def http_session():
    s = requests.Session()
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
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# =========================
# Helpers
# =========================
def safe_snippet(x, n=800):
    if x is None:
        return ""
    s = str(x)
    return s[:n] + ("…" if len(s) > n else "")


def money_fmt(value, currency="GBP"):
    if value is None:
        return "—"
    try:
        if isinstance(value, float) and math.isnan(value):
            return "—"
    except Exception:
        pass
    return f"{currency} {value:,.2f}"


def tcgdex_card_image(base_url, quality="low", ext="webp"):
    """
    TCGdex image URLs may come without an extension; that's expected.
    To get the final asset you append /{quality}.{extension} (e.g. /low.webp). [1](https://developer.ebay.com/support/kb-article?KBid=1445)
    """
    if not base_url:
        return None
    if base_url.endswith((".png", ".webp", ".jpg", ".jpeg")):
        return base_url
    return f"{base_url}/{quality}.{ext}"


def card_tile_html(image_url, name, subtitle, badge_text):
    safe_name = html.escape(name or "")
    safe_sub = html.escape(subtitle or "")
    safe_badge = html.escape(badge_text or "")

    if image_url:
        safe_src = html.escape(image_url, quote=True)
        img_html = f"""
        <img src="{safe_src}" style="width:100%; height:310px; object-fit:contain; display:block;" loading="lazy" />
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


# =========================
# PokémonTCG (primary)
# =========================
@st.cache_data(ttl=60 * 30)
def pokemontcg_search_cards(query, page_size, timeout):
    s = http_session()
    headers = {"Accept": "application/json", "User-Agent": "pokemon-search/1.0"}
    if POKEMONTCG_API_KEY:
        headers["X-Api-Key"] = POKEMONTCG_API_KEY

    params = {
        "q": f'name:"{query}"',
        "page": 1,
        "pageSize": page_size,
        "select": "id,name,number,set,images,tcgplayer",
        "orderBy": "set.releaseDate",
    }

    try:
        r = s.get(POKEMONTCG_ENDPOINT, headers=headers, params=params, timeout=timeout)
    except requests.exceptions.ReadTimeout as e:
        return {"ok": False, "status": None, "error": "PokémonTCG timeout", "details": str(e), "data": []}
    except Exception as e:
        return {"ok": False, "status": None, "error": "PokémonTCG network error", "details": str(e), "data": []}

    if r.status_code == 429:
        return {"ok": False, "status": 429, "error": "PokémonTCG rate limit", "details": safe_snippet(r.text), "data": []}
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": f"PokémonTCG HTTP {r.status_code}", "details": safe_snippet(r.text), "data": []}

    payload = r.json()
    return {"ok": True, "status": 200, "error": None, "details": None, "data": payload.get("data", [])}


def normalize_from_pokemontcg(card):
    set_obj = card.get("set") or {}
    images = card.get("images") or {}
    return {
        "source": "pokemontcg",
        "id": card.get("id"),
        "name": card.get("name", "Unknown"),
        "number": card.get("number", "?"),
        "set_name": set_obj.get("name", "Unknown set"),
        "image_small": images.get("small"),
        "image_large": images.get("large"),
        "raw": card,
    }


# =========================
# TCGdex (fallback)
# =========================
@st.cache_data(ttl=60 * 30)
def tcgdex_search_cards(query, page_size, timeout):
    s = http_session()
    params = {"name": query}
    try:
        r = s.get(TCGDEX_LIST_ENDPOINT, params=params, timeout=timeout)
    except requests.exceptions.ReadTimeout as e:
        return {"ok": False, "status": None, "error": "TCGdex timeout", "details": str(e), "data": []}
    except Exception as e:
        return {"ok": False, "status": None, "error": "TCGdex network error", "details": str(e), "data": []}

    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": f"TCGdex HTTP {r.status_code}", "details": safe_snippet(r.text), "data": []}

    data = r.json()
    return {"ok": True, "status": 200, "error": None, "details": None, "data": data[:page_size]}


@st.cache_data(ttl=60 * 60)
def tcgdex_get_card(card_id, timeout):
    s = http_session()
    url = TCGDEX_CARD_ENDPOINT.format(card_id=card_id)
    r = s.get(url, timeout=timeout)
    if r.status_code >= 400:
        return None
    return r.json()


def normalize_from_tcgdex(card):
    # image is optional in TCGdex; may be missing [2](https://developer.ebay.com/Devzone/finding/CallRef/Samples/findItemsByCategory_aspectHist_out_json.txt)
    base_image = card.get("image")
    return {
        "source": "tcgdex",
        "id": card.get("id"),
        "name": card.get("name", "Unknown"),
        "number": card.get("localId", "?"),
        "set_name": (card.get("set") or {}).get("name", "Unknown set"),
        "image_small": tcgdex_card_image(base_image, "low", "webp"),
        "image_large": tcgdex_card_image(base_image, "high", "webp"),
        "raw": card,
    }


# =========================
# eBay sold (best-effort)
# =========================
def build_ebay_keywords(norm_card):
    name = (norm_card.get("name") or "").strip()
    number = str(norm_card.get("number") or "").strip()
    set_name = (norm_card.get("set_name") or "").strip()
    excluded = "-lot -bundle -collection -proxy -digital -code"
    return f'"{name}" "{set_name}" "{number}" pokemon card {excluded}'


@st.cache_data(ttl=60 * 20)
def ebay_find_completed_items(keywords, days, entries, include_shipping):
    if not EBAY_APP_ID:
        return {"ok": False, "error": "Missing EBAY_APP_ID in secrets", "items": []}

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
        return {"ok": False, "error": str(e), "items": []}

    resp = payload.get("findCompletedItemsResponse", [])
    if not resp:
        return {"ok": False, "error": "Unexpected eBay response shape", "items": []}

    ack = (resp[0].get("ack") or [""])[0]
    if ack != "Success":
        return {"ok": False, "error": safe_snippet(resp[0].get("errorMessage")), "items": []}

    items = (resp[0].get("searchResult", [{}])[0].get("item")) or []
    out = []
    for it in items:
        title = (it.get("title") or [""])[0]
        url = (it.get("viewItemURL") or [""])[0]
        end_time = ((it.get("listingInfo") or [{}])[0].get("endTime") or [""])[0]

        selling_status = (it.get("sellingStatus") or [{}])[0]
        price_obj = (selling_status.get("currentPrice") or [{}])[0]
        raw_price = price_obj.get("__value__", price_obj.get("#text", 0.0))
        currency = price_obj.get("@currencyId", price_obj.get("currencyId", "GBP"))
        try:
            price = float(raw_price or 0.0)
        except Exception:
            price = 0.0

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
        out.append({
            "title": title,
            "end_time": end_time,
            "price": price,
            "shipping": ship_cost,
            "total_price": total_price,
            "currency": currency,
            "url": url
        })

    return {"ok": True, "error": None, "items": out}


def summarize_prices(items):
    if not items:
        return None
    df = pd.DataFrame(items)
    cur = df["currency"].mode().iloc[0] if "currency" in df.columns and len(df) else "GBP"
    df = df[df["currency"] == cur]
    return {
        "currency": cur,
        "count": int(len(df)),
        "high": float(df["total_price"].max()),
        "low": float(df["total_price"].min()),
        "avg": float(df["total_price"].mean()),
        "df": df.sort_values("end_time", ascending=False),
    }


# =========================
# UI
# =========================
st.title("Pokémon Card Sold Prices (Grid + Stats)")

with st.expander("ℹ️ Notes", expanded=False):
    st.write(
        "TCGdex image URLs may not include file extensions; append /low.webp or /high.webp to fetch assets. "
        "Some TCGdex entries may have no image field at all."
    )

st.sidebar.header("Filters")
days = st.sidebar.select_slider("Timescale (days)", options=[7, 14, 30, 60, 90], value=30)
include_shipping = st.sidebar.toggle("Include shipping", value=True)
max_cards = st.sidebar.slider("Max variants", 6, 36, 18, step=6)
max_sales = st.sidebar.slider("Max sold listings (eBay)", 10, 100, 40, step=10)
do_ebay = st.sidebar.toggle("Fetch eBay sold (may fail)", value=True)

connect_timeout = st.sidebar.slider("Connect timeout (sec)", 2, 20, 5)
read_timeout = st.sidebar.slider("Read timeout (sec)", 10, 120, 60)
timeout_tuple = (connect_timeout, read_timeout)

with st.form("search_form"):
    query = st.text_input("Search for a card", value="Charizard")
    submitted = st.form_submit_button("Search")

if not submitted:
    st.stop()

query = query.strip()
if not query:
    st.warning("Enter a search term.")
    st.stop()

with st.spinner("Searching…"):
    primary = pokemontcg_search_cards(query, page_size=max_cards, timeout=timeout_tuple)

cards = []
provider = None

if primary["ok"]:
    provider = "PokémonTCG"
    cards = [normalize_from_pokemontcg(c) for c in primary["data"]]
else:
    with st.spinner("Falling back to TCGdex…"):
        fb = tcgdex_search_cards(query, page_size=max_cards, timeout=timeout_tuple)

    if fb["ok"] and fb["data"]:
        provider = "TCGdex"
        full = []
        for brief in fb["data"]:
            cid = brief.get("id")
            if cid:
                c = tcgdex_get_card(cid, timeout=timeout_tuple)
                if c:
                    full.append(c)
        cards = [normalize_from_tcgdex(c) for c in full]
    else:
        st.error("Both providers failed.")
        st.code(f"PokémonTCG: {primary.get('error')}\n{primary.get('details')}", language="text")
        st.code(f"TCGdex: {fb.get('error')}\n{fb.get('details')}", language="text")
        st.stop()

if not cards:
    st.warning("No results.")
    st.stop()

st.caption(f"Provider used: **{provider}**")

cols = st.columns(6)
labels = []
lookup = {}

for i, card in enumerate(cards):
    subtitle = f"{card.get('number')} • {card.get('set_name')}"
    img = card.get("image_small") or card.get("image_large")

    badge = "—"
    ebay_summary = None
    ebay_err = None
    keywords = None

    if do_ebay:
        keywords = build_ebay_keywords(card)
        ebay = ebay_find_completed_items(keywords, days, max_sales, include_shipping)
        if ebay["ok"]:
            ebay_summary = summarize_prices(ebay["items"])
            badge = f"{ebay_summary['currency']} {ebay_summary['high']:.2f}" if ebay_summary else "No sold"
        else:
            ebay_err = ebay["error"]
            badge = "Sold blocked"

    with cols[i % 6]:
        st.markdown(card_tile_html(img, card.get("name"), subtitle, badge), unsafe_allow_html=True)

    label = f"{card.get('name')} — {subtitle}"
    labels.append(label)
    lookup[label] = {"card": card, "keywords": keywords, "summary": ebay_summary, "err": ebay_err}

st.divider()
st.header("Details")

sel = st.selectbox("Select a card", labels)
obj = lookup[sel]
card = obj["card"]

left, right = st.columns([1, 2])
with left:
    st.subheader(card.get("name"))
    st.caption(f"{card.get('number')} • {card.get('set_name')}")
    st.image(card.get("image_large") or card.get("image_small"), use_container_width=True)
    if obj["keywords"]:
        st.caption("eBay keywords")
        st.code(obj["keywords"])

with right:
    if do_ebay and obj["summary"]:
        s = obj["summary"]
        c = s["currency"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Highest", money_fmt(s["high"], c))
        m2.metric("Average", money_fmt(s["avg"], c))
        m3.metric("Lowest", money_fmt(s["low"], c))
        m4.metric("Samples", str(s["count"]))
    elif do_ebay and obj["err"]:
        st.error("eBay sold lookup failed / blocked.")
        st.code(obj["err"])
    else:
        st.info("No eBay stats to show.")
