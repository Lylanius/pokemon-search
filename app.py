import streamlit as st
import requests
import statistics
from datetime import datetime, timedelta

st.set_page_config(page_title="Pokémon Card Price Tracker", layout="wide")

# -------------------------------
# Helper Functions
# -------------------------------

@st.cache_data(ttl=3600)
def fetch_cards_from_scryfall(name):
    url = f"https://api.scryfall.com/cards/search?q={name}"
    resp = requests.get(url)
    if resp.status_code != 200:
        return []
    return resp.json().get("data", [])

@st.cache_data(ttl=3600)
def fetch_sold_prices_ebay(card_name, appid):
    url = "https://svcs.ebay.com/services/search/FindingService/v1"
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": appid,
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": f"pokemon {card_name}",
        "categoryId": "183454",  # Pokémon TCG
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
Enter the name of a Pokémon card and see recent sold prices, along with images and stats.
""")

col1, col2 = st.columns([3,1])
with col1:
    card_query = st.text_input("Enter Pokémon card name:")
with col2:
    timescale = st.selectbox("Recent sales timescale:", ["7 Days", "30 Days", "90 Days", "365 Days"])

# ✅ Load eBay App ID from Streamlit secrets
try:
    EBAY_APP_ID = st.secrets["ebay"]["app_id"]
except KeyError:
    st.error("eBay App ID not found in secrets! Please add it in Streamlit Cloud settings.")
    st.stop()

if st.button("Search"):
    if not card_query:
        st.error("Please enter a card name.")
    else:
        st.info("Searching cards...")

        # -- 1) Fetch cards from Scryfall
        cards = fetch_cards_from_scryfall(card_query)
        if not cards:
            st.error("No cards found.")
        else:
            st.success(f"Found {len(cards)} cards.")

            days = timescale_to_days(timescale)

            # Display cards in 3-column grid
            for i in range(0, len(cards), 3):
                cols = st.columns(3)
                for j, card in enumerate(cards[i:i+3]):
                    img_url = card.get("images", {}).get("small", "")
                    name = card.get("name", "Unknown")
                    set_code = card.get("set", "")
                    number = card.get("collector_number", "")

                    items = fetch_sold_prices_ebay(f"{name} {set_code} {number}", EBAY_APP_ID)
                    prices = extract_prices_from_ebay(items, days)

                    highest = max(prices) if prices else None
                    lowest = min(prices) if prices else None
                    avg = statistics.mean(prices) if prices else None

                    with cols[j]:
                        # Image with overlayed highest price
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
