import streamlit as st
import requests
import statistics
from datetime import datetime, timedelta
import urllib.parse

st.set_page_config(page_title="Pokémon Card Price Tracker", layout="wide")

# -------------------------------
# Helper Functions
# -------------------------------

@st.cache_data(ttl=3600)
def fetch_cards_from_scryfall(name):
    """
    Fetch Pokémon cards from Scryfall using partial/fuzzy name search.
    Works for 'charizard', 'charizard gx', etc.
    """
    name = name.strip()
    if not name:
        return []

    # Fuzzy search using wildcard *
    query = urllib.parse.quote(f'name:{name}*')
    url = f"https://api.scryfall.com/cards/search?q={query}'

    resp = requests.get(url)
    if resp.status_code != 200:
        return []

    data = resp.json()
    cards = data.get("data", [])

    # Handle pagination
    while data.get("has_more"):
        next_page = data.get("next_page")
        if not next_page:
            break
        resp = requests.get(next_page)
        data = resp.json()
        cards.extend(data.get("data", []))

    return cards

@st.cache_data(ttl=1800)
def fetch_sold_prices_ebay(card_name, appid):
    """
    Fetch sold listings from eBay Completed Items API
    """
    url = "https://svcs.ebay.com/services/search/FindingService/v1"
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": appid,
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": f"pokemon {card_name}",
        "categoryId": "183454",
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "paginationInput.entriesPerPage": "100"
    }
    r = requests.get(url, params=params)
    try:
        items = r.json()["findCompletedItemsResponse"][0]["searchResult"][0]["item"]
        return items
    except:
        return []

def extract_prices_from_ebay(items, days):
    prices = []
    cutoff = datetime.now() - timedelta(days=days)
    for it in items:
        try:
            end_time = datetime.strptime(it["listingInfo"][0]["endTime"][0], "%Y-%m-%dT%H:%M:%S.%fZ")
            if end_time < cutoff:
                continue
            price = float(it["sellingStatus"][0]["currentPrice"][0]["__value__"])
            prices.append(price)
        except:
            continue
    return prices

def timescale_to_days(timescale_str):
    return int(timescale_str.split()[0])

# -------------------------------
# Streamlit UI
# -------------------------------

st.title("🎴 Pokémon Card Price Tracker")
st.markdown("""
Enter a Pokémon card name to see recent sold prices, images, and stats.
""")

col1, col2 = st.columns([3,1])
with col1:
    card_query = st.text_input("Enter Pokémon card name:")
with col2:
    timescale = st.selectbox("Recent sales timescale:", ["7 Days", "30 Days", "90 Days", "365 Days"])

# Load eBay App ID from Streamlit secrets
try:
    EBAY_APP_ID = st.secrets["ebay"]["app_id"]
except KeyError:
    st.error("eBay App ID not found in secrets! Please add it in Streamlit Cloud settings.")
    st.stop()

# Load More functionality
if "card_index" not in st.session_state:
    st.session_state["card_index"] = 0

CARDS_PER_LOAD = 12  # cards per batch

if st.button("Search") or st.session_state.get("search_triggered", False):
    if card_query:
        st.session_state["search_triggered"] = True
        st.session_state["card_index"] = 0

        # Fetch Scryfall cards
        cards = fetch_cards_from_scryfall(card_query)
        if not cards:
            st.error("No cards found.")
        else:
            st.session_state["cards"] = cards
    else:
        st.error("Please enter a card name.")

# Display cards if any
cards = st.session_state.get("cards", [])
if cards:
    days = timescale_to_days(timescale)
    start = st.session_state["card_index"]
    end = start + CARDS_PER_LOAD
    batch = cards[start:end]

    for i in range(0, len(batch), 3):
        cols = st.columns(3)
        for j, card in enumerate(batch[i:i+3]):
            img_url = card.get("images", {}).get("small", "")
            name = card.get("name", "Unknown")
            set_code = card.get("set", "")
            number = card.get("collector_number", "")

            # Fetch eBay prices
            items = fetch_sold_prices_ebay(f"{name} {set_code} {number}", EBAY_APP_ID)
            prices = extract_prices_from_ebay(items, days)

            highest = max(prices) if prices else None
            lowest = min(prices) if prices else None
            avg = statistics.mean(prices) if prices else None

            with cols[j]:
                if img_url:
                    if highest:
                        st.markdown(f"""
                        <div style="position: relative; display: inline-block;">
                          <img src="{img_url}" width="200"/>
                          <div style="position: absolute; top: 8px; right: 8px; background: rgba(255,255,255,0.9); padding: 4px;
                                      border-radius: 4px; font-weight: bold; font-size:14px;">
                              ${highest:.2f}
                          </div>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.image(img_url, width=200)

                st.markdown(f"**{name} ({set_code}-{number})**")
                if prices:
                    st.write(f"Lowest: ${lowest:.2f}")
                    st.write(f"Average: ${avg:.2f}")
                else:
                    st.write("No recent sales")

    # Load more button
    if end < len(cards):
        if st.button("Load more cards"):
            st.session_state["card_index"] += CARDS_PER_LOAD
            st.experimental_rerun()
