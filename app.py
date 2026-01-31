# app.py
import streamlit as st
import requests
import pandas as pd
import altair as alt
from statistics import median
import os

st.set_page_config(page_title="Pokémon Card Price Checker", layout="wide")
st.markdown(
    """
    <div style="background: linear-gradient(135deg, #ff6b6b 0%, #ee5a6f 100%);
                padding: 30px; border-radius: 12px; color: white; text-align: center;">
        <h1>🎴 Pokémon Card Price Checker (UK)</h1>
        <p>Live pricing from recent <strong>eBay sold</strong> listings</p>
        <p style="font-size: 0.85rem; opacity: 0.9;">Runs via Streamlit (Python backend)</p>
    </div>
    """, unsafe_allow_html=True
)

# --- Sidebar options ---
st.sidebar.header("Search Options")
card_query = st.sidebar.text_input("Enter card name or number", value="Charizard VMAX")
num_results = st.sidebar.slider("Listings to fetch", min_value=5, max_value=200, value=30)
trim_pct = st.sidebar.slider("Trim outliers (%)", min_value=0, max_value=40, value=10)
bins_count = st.sidebar.slider("Histogram bins", min_value=5, max_value=40, value=12)

# --- Apify Token from environment ---
APIFY_TOKEN = "apify_api_0gpRxeCr7yuInE7EVg4Th3wukB6LhJ0lSI0l"

if not APIFY_TOKEN:
    st.warning("⚠️ APIFY_TOKEN is not set! Add it in Streamlit secrets or as an environment variable.")

if st.sidebar.button("Search") and card_query.strip():
    st.info(f"Fetching sold listings for '{card_query}'…")

    url = f"https://api.apify.com/v2/acts/caffein.dev~ebay-sold-listings/run-sync-get-dataset-items?token={APIFY_TOKEN}&clean=1"
    payload = {"keyword": card_query, "maxItems": num_results}

    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            st.error("No sold listings found. Try another search.")
        else:
            # Process data into DataFrame
            df = pd.DataFrame([{
                "Title": item.get("title"),
                "Price": float(item.get("price") or item.get("soldPrice") or 0),
                "Currency": item.get("currency") or "GBP",
                "Date": item.get("dateEnded") or item.get("endDate") or item.get("date"),
                "Condition": item.get("condition") or item.get("subtitle"),
                "URL": item.get("url") or item.get("itemUrl")
            } for item in data if item.get("price")])

            if df.empty:
                st.error("No valid prices found.")
            else:
                # Trim outliers
                df_sorted = df.sort_values("Price")
                cut = int(len(df_sorted) * (trim_pct / 100))
                if cut > 0:
                    df_trimmed = df_sorted.iloc[cut:-cut]
                else:
                    df_trimmed = df_sorted

                # Stats
                avg_price = df_trimmed["Price"].mean()
                med_price = median(df_trimmed["Price"])
                min_price = df_trimmed["Price"].min()
                max_price = df_trimmed["Price"].max()

                st.subheader("Price Summary")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Average (trimmed)", f"£{avg_price:.2f}")
                col2.metric("Median", f"£{med_price:.2f}")
                col3.metric("Lowest", f"£{min_price:.2f}")
                col4.metric("Highest", f"£{max_price:.2f}")

                # Histogram
                st.subheader("Price Distribution")
                chart = alt.Chart(df_trimmed).mark_bar().encode(
                    alt.X("Price:Q", bin=alt.Bin(maxbins=bins_count), title="Price (GBP)"),
                    alt.Y('count()', title='Listings')
                )
                st.altair_chart(chart, use_container_width=True)

                # Table of recent sold listings
                st.subheader(f"Recent Sold Listings ({len(df)})")
                df_display = df.copy()
                df_display["Link"] = df_display["URL"].apply(lambda x: f"[Link]({x})" if x else "")
                st.dataframe(df_display[["Title", "Price", "Date", "Condition", "Link"]])

    except Exception as e:
        st.error(f"Error fetching data: {e}")

