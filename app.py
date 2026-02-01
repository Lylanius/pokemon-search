# app.py
import os
import base64
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone
import gradio as gr
from dotenv import load_dotenv

# --------------------------
# Load environment variables
# --------------------------
load_dotenv()

POKEMON_API_KEY = os.getenv("POKEMON_API_KEY")
EBAY_APP_ID     = os.getenv("EBAY_APP_ID")
EBAY_CERT_ID    = os.getenv("EBAY_CERT_ID")
DEFAULT_COMPS_LIMIT = 20

# --------------------------
# API ROOT
# --------------------------
POKEMON_API_ROOT = "https://www.pokemonpricetracker.com/api/v2"

# --------------------------
# Pokémon Price Tracker API
# --------------------------
def _auth_header():
    if not POKEMON_API_KEY:
        raise RuntimeError("POKEMON_API_KEY is not set in .env")
    return {"Authorization": f"Bearer {POKEMON_API_KEY}"}

def search_card(query: str):
    r = requests.get(f"{POKEMON_API_ROOT}/cards", headers=_auth_header(), params={"q": query}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    return payload.get("cards") or payload.get("data") or []

def get_card_price(card_id: str):
    r = requests.get(f"{POKEMON_API_ROOT}/cards/{card_id}", headers=_auth_header(), timeout=20)
    r.raise_for_status()
    return r.json()

def get_price_history(card_id: str, days: int = 30, limit: int = 20):
    params = {"days": int(days), "limit": int(limit)}
    r = requests.get(f"{POKEMON_API_ROOT}/cards/{card_id}/history", headers=_auth_header(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "history" in data:
        data = data["history"]
    return (data or [])[:limit]

# --------------------------
# eBay Browse API (Live listings)
# --------------------------
def get_ebay_token():
    if not EBAY_APP_ID or not EBAY_CERT_ID:
        return None
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
# Helpers
# --------------------------
def parse_iso(dt_str: str):
    if not dt_str:
        return None
    s = dt_str.replace("Z","+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def fmt_date(dt_str: str):
    try:
        return parse_iso(dt_str).strftime("%d %b %Y")
    except Exception:
        return str(dt_str)

def price_link(label, price):
    try:
        p = float(price)
        return f"{label}: £{p:.2f}<br>"
    except Exception:
        return f"{label}: —<br>"

def build_gallery_tiles(cards):
    tiles = []
    for c in cards[:36]:
        img = c.get("image","https://via.placeholder.com/150")
        name = c.get("name","Unknown")
        mp = c.get("market_price",0) or 0
        tiles.append((img, f"{name}\n£{mp:.2f}"))
    return tiles

# --------------------------
# Show card details
# --------------------------
def show_card_data(card_id: str, comps_limit: int, days: int):
    card_price = get_card_price(card_id)
    name = card_price.get("name","Unknown")
    market_price = card_price.get("market_price",0)
    low_price = card_price.get("low_price",0)
    high_price = card_price.get("high_price",0)

    history = get_price_history(card_id, days=int(days), limit=int(comps_limit)) or []
    dates, prices = [], []
    for h in history:
        dt = parse_iso(h.get("date") or h.get("sold_at") or h.get("timestamp"))
        pr = h.get("price") or h.get("sold_price")
        if dt and pr is not None:
            dates.append(dt)
            prices.append(float(pr))

    # Sold stats HTML
    sold_html = (
        "<div class='stat-display'>"
        + price_link("MARKET", market_price)
        + price_link("LOW", low_price)
        + price_link("HIGH", high_price)
        + f"VOLUME: {len(prices)}</div>"
    )

    # Plot sold prices
    sold_plot = None
    if dates:
        fig, ax = plt.subplots(figsize=(7,3.5), facecolor='#1a1a1a')
        ax.set_facecolor("#1a1a1a")
        ax.scatter(dates, prices, color='#00ff00', s=40, label="Sold")
        if len(prices) > 1:
            x_nums = np.array([d.timestamp() for d in dates])
            y_vals = np.array(prices)
            x_norm = x_nums - x_nums.mean()
            coeffs = np.polyfit(x_norm, y_vals, 1)
            trend_y = np.polyval(coeffs, x_norm)
            ax.plot(dates, trend_y, color='#ffaa00', linestyle='--', linewidth=1.5, label="Trend")
        ax.axhline(float(market_price or 0), color='cyan', linestyle=':', linewidth=1, label=f"Market £{float(market_price or 0):.0f}")
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        fig.autofmt_xdate(rotation=30, ha='right')
        ax.tick_params(colors='white', labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#555')
        ax.set_ylabel("Price (£)", color='white', fontsize=9)
        ax.legend(loc='upper left', fontsize=7, facecolor='#1a1a1a', edgecolor='#555', labelcolor='white')
        ax.set_title(f"Sold Prices: {name}", color='white', fontsize=10)
        plt.tight_layout()
        sold_plot = fig

    # Sold dataframe
    sold_df = pd.DataFrame(
        [{"Price": p, "Date": d.strftime("%Y-%m-%d")} for p,d in sorted(zip(prices, dates), key=lambda t: t[1], reverse=True)]
    )

    # eBay live listings
    live_items = get_ebay_live_listings(name, limit=10)
    if live_items:
        live_html = "<div class='stat-display'>"
        for li in live_items:
            live_html += f"<a href='{li['url']}' target='_blank'>{li['title']}</a> - £{li['price']:.2f}<br>"
        live_html += "</div>"
    else:
        live_html = "<div class='stat-display'>No live listings found</div>"

    return sold_html, sold_plot, sold_df, live_html

# --------------------------
# Gradio UI
# --------------------------
css = """
.pokedex-frame {
    background: #dc0a2d;
    border-radius: 20px;
    padding: 20px;
    border-bottom: 12px solid #8b0000;
    box-shadow: 0 10px 0 #000;
    color: white;
}
.stat-display {
    background: #1a1a1a;
    color: #00ff00;
    font-family: 'IBM Plex Mono', monospace;
    padding: 15px;
    border-radius: 12px;
    border: 2px solid #555;
    font-size: 14px;
    line-height: 1.6;
}
"""

with gr.Blocks(css=css) as demo:
    cards_state = gr.State([])

    with gr.Column(elem_classes="pokedex-frame"):
        gr.HTML("<h2 style='color:white;'>POKÉ‑QUANT MASTER vGrid</h2>")
        with gr.Row():
            search_in = gr.Textbox(label="SEARCH CARD", value="Pikachu")
            comps_in  = gr.Slider(3,50,value=DEFAULT_COMPS_LIMIT,step=1,label="SOLD COMPS")
            days_in   = gr.Radio(["30","60","90"],label="SEARCH WINDOW (DAYS)",value="90")

        btn_search = gr.Button("SEARCH CARDS", variant="primary")

        gallery = gr.Gallery(label="Results", columns=6, height=350, allow_preview=False)

        with gr.Tabs():
            with gr.Tab("📈 Live Listings"):
                live_html = gr.HTML("<div class='stat-display'>Press search and select a card…</div>")

            with gr.Tab("🏷️ Sold History"):
                with gr.Row():
                    sold_html = gr.HTML("<div class='stat-display'>Select a card to see sold history</div>")
                    sold_plot = gr.Plot()
                sold_df = gr.Dataframe(label="SOLD DATA LOG")

    def do_search(q):
        cards = search_card(q.strip())
        tiles = build_gallery_tiles(cards)
        return tiles, cards

    btn_search.click(do_search, [search_in], [gallery, cards_state])

    def on_gallery_select(evt: gr.SelectData, cards, comps, days):
        idx = evt.index
        if not cards or idx is None or idx >= len(cards):
            return (gr.update(), None, pd.DataFrame(), gr.update())
        card_id = cards[idx].get("id")
        return show_card_data(card_id, int(comps), int(days))

    gallery.select(on_gallery_select, [cards_state, comps_in, days_in],
                   [sold_html, sold_plot, sold_df, live_html])

demo.launch()
