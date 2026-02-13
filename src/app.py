import streamlit as st

# Define the pages
validator_page = st.Page("pages/validator.py", title="RDF Sync Validator", icon="⚖️")
transpiler_page = st.Page("pages/transpiler.py", title="Visualize Transpiler", icon="🔗")

# Create navigation
pg = st.navigation([validator_page, transpiler_page])

# Shared configuration (optional)
st.set_page_config(page_title="Lindas migration tools", layout="wide")

# Run the selected page
pg.run()