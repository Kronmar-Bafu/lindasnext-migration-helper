import streamlit as st
import requests
import json

# --- CONFIGURATION ---
STARDOG_EP = "https://lindas.admin.ch/query"
GRAPHDB_EP = "https://lindas.int.cz-aws.net/query"
VISUALIZE_BASE = "https://visualize.admin.ch"
# Using the production preview often works best, but you can switch to 'test' or 'int'
PREVIEW_BASE = "https://test.visualize.admin.ch/de/preview"

st.set_page_config(layout="wide")
st.title("🔄 Stardog ➔ GraphDB Transpiler")


# --- HELPER FUNCTIONS ---
def extract_config(url):
    try:
        slug = url.split('/')[-1]
        # We fetch from the API matching the URL provided
        api_base = "https://int.visualize.admin.ch" if "int.visualize" in url else "https://visualize.admin.ch"

        resp = requests.get(f"{api_base}/api/config/{slug}")
        resp.raise_for_status()
        full_payload = resp.json()

        # Robust extraction: checks for nested data structure
        config = full_payload.get('data', {}).get('data', full_payload.get('data', {}))
        return config

    except Exception as e:
        st.error(f"Error fetching config: {e}")
        return None


def generate_html(config, endpoint, unique_id):
    """
    Generates the HTML/JS blob.
    Args:
        unique_id: Critical for side-by-side rendering so JS targets the correct frame.
    """
    # 1. Update Endpoint
    config["dataSource"]["url"] = endpoint
    config["state"] = "CONFIGURING_CHART"

    # 2. Serialize
    config_json = json.dumps(config)

    # 3. Construct HTML with unique IDs
    html_code = f"""
    <div style="height: 600px; width: 100%; border: 1px solid #ddd; border-radius: 4px; overflow: hidden;">
        <iframe id="vis-frame-{unique_id}" src="{PREVIEW_BASE}" width="100%" height="100%" frameborder="0"></iframe>
    </div>

    <script>
        (function() {{
            const iframe = document.getElementById('vis-frame-{unique_id}');
            const configPayload = {config_json};

            window.addEventListener('message', function(e) {{
                // Check if the message comes from our specific iframe
                if (e.data && e.data.type === 'ready' && e.source === iframe.contentWindow) {{
                    console.log("Visualize ({unique_id}) is ready. Injecting config...");
                    iframe.contentWindow.postMessage(configPayload, '*');
                }}
            }});
        }})();
    </script>
    """
    return html_code


# --- MAIN UI ---

# 1. Input Section (Full Width)
source_url = st.text_input("Source Chart URL:", "https://visualize.admin.ch/de/v/i5pRyeLumI3I")

# 2. Action Button
if st.button("🚀 Transpile & Compare", type="primary"):
    st.session_state['run_comparison'] = True

# --- LOGIC & RENDERING ---
if st.session_state.get('run_comparison') and source_url:

    # Fetch config once
    base_config = extract_config(source_url)

    if base_config:
        st.divider()
        col1, col2 = st.columns(2)

        # Left Column: Stardog (Original Endpoint usually)
        with col1:
            st.subheader("Stardog (Current)")
            # We pass a copy() so we don't mutate the config for the second column
            html_stardog = generate_html(base_config.copy(), STARDOG_EP, "stardog")
            st.components.v1.html(html_stardog, height=600)

        # Right Column: GraphDB (Target Endpoint)
        with col2:
            st.subheader("GraphDB (Target)")
            html_graphdb = generate_html(base_config.copy(), GRAPHDB_EP, "graphdb")
            st.components.v1.html(html_graphdb, height=600)

        # Debugging JSON (Placed here so it updates with the state)
        with st.expander("View Raw Configuration"):
            st.json(base_config)