import streamlit as st
import requests
import json

# --- CONFIGURATION ---
STARDOG_EP = "https://lindas.admin.ch/query"
GRAPHDB_EP = "https://int.cached.lindas.admin.ch/query"
PREVIEW_BASE = "https://test.visualize.admin.ch/de/preview"

st.set_page_config(layout="wide")
st.title("🔄 Stardog ➔ GraphDB Transpiler")


# --- HELPER FUNCTIONS ---
def extract_config(url):
    try:
        slug = url.split('/')[-1]
        api_base = "https://int.visualize.admin.ch" if "int.visualize" in url else "https://visualize.admin.ch"

        resp = requests.get(f"{api_base}/api/config/{slug}")
        resp.raise_for_status()
        full_payload = resp.json()
        return full_payload.get('data', {}).get('data', full_payload.get('data', {}))

    except Exception as e:
        st.error(f"Error fetching config: {e}")
        return None


def generate_html(config, endpoint, unique_id):
    config["dataSource"]["url"] = endpoint
    config["state"] = "CONFIGURING_CHART"
    config_json = json.dumps(config)

    return f"""
    <div style="height: 600px; width: 100%; border: 1px solid #ddd; border-radius: 4px; overflow: hidden;">
        <iframe id="vis-frame-{unique_id}" src="{PREVIEW_BASE}" width="100%" height="100%" frameborder="0"></iframe>
    </div>
    <script>
        (function() {{
            const iframe = document.getElementById('vis-frame-{unique_id}');
            const configPayload = {config_json};
            window.addEventListener('message', function(e) {{
                if (e.data && e.data.type === 'ready' && e.source === iframe.contentWindow) {{
                    iframe.contentWindow.postMessage(configPayload, '*');
                }}
            }});
        }})();
    </script>
    """


# --- MAIN UI ---
source_url = st.text_input("1. Source Chart URL:", "https://visualize.admin.ch/de/v/i5pRyeLumI3I")

if st.button("🚀 Run Comparison", type="primary"):
    st.session_state['run'] = True

if st.session_state.get('run') and source_url:

    config = extract_config(source_url)
    if config:
        st.divider()

        # --- B. VISUAL SECTION ---
        st.subheader("Visual Comparison")
        c1, c2 = st.columns(2)

        with c1:
            st.caption(f"Endpoint: {STARDOG_EP}")
            st.components.v1.html(generate_html(config.copy(), STARDOG_EP, "sd"), height=600)

        with c2:
            st.caption(f"Endpoint: {GRAPHDB_EP}")
            st.components.v1.html(generate_html(config.copy(), GRAPHDB_EP, "gdb"), height=600)