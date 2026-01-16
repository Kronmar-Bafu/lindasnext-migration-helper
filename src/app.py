import streamlit as st
import yaml
import requests
import unicodedata
from rdflib import Graph
from rdflib.compare import to_isomorphic, graph_diff
from pathlib import Path
import os

# 1. Absolute Path Discovery
# This finds the directory where THIS file (app.py) is located
BASE_DIR = Path(__file__).resolve().parent


@st.cache_data
def load_config():
    # Try to find presets.yaml in the same folder as app.py
    config_path = BASE_DIR / "presets.yaml"

    if not config_path.exists():
        # DEBUG: If it fails, show the user the file system
        st.error(f"Critical Error: File not found at {config_path}")
        st.write("Files found in current directory:", os.listdir(BASE_DIR))
        st.stop()

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# 2. Execution
try:
    # Set page config FIRST
    st.set_page_config(page_title="RDF Sync Validator", page_icon="⚖️", layout="wide")

    # Load Config
    config = load_config()
except Exception as e:
    st.error(f"App failed to start: {e}")
    st.stop()

# --- Sidebar ---
with st.sidebar:
    st.header("1. Environments")
    ep_names_st = [e['name'] for e in config['endpoints_stardog']]
    st_env = st.selectbox("Stardog Instance", ep_names_st)
    st_endpoint = next(e['url'] for e in config['endpoints_stardog'] if e['name'] == st_env)
    st.caption(f"Connected to: `{st_endpoint}`")

    ep_names_gdb = [e['name'] for e in config['endpoints_graphdb']]
    gdb_env = st.selectbox("GraphDB Instance", ep_names_gdb)
    gdb_endpoint = next(e['url'] for e in config['endpoints_graphdb'] if e['name'] == gdb_env)
    st.caption(f"Connected to: `{gdb_endpoint}`")

    st.divider()

    st.header("2. Datasets")
    preset_names = [p['name'] for p in config['presets']]
    selection = st.selectbox("Select Preset", preset_names)
    current_preset = next(p for p in config['presets'] if p['name'] == selection)

    st_graph_iri = st.text_input("Stardog Graph", value=current_preset['st_graph'])
    gdb_graph_iri = st.text_input("GraphDB Graph", value=current_preset['gdb_graph'])

    st.header("3. Filter Settings")
    # Use the labels from YAML as options
    selected_labels = st.multiselect(
        "Exclude Predicates",
        options=list(FILTER_MAP.keys()),
        default=current_preset.get('default_filters', [])
    )

    # Map selected labels back to URIs
    excluded_uris = [FILTER_MAP[label] for label in selected_labels]

# --- Logic ---
def fetch_clean_graph(endpoint, graph_iri, filter_uris):
    filter_clause = ""
    if filter_uris:
        # Format the URIs for SPARQL: <uri1>, <uri2>
        uri_list = ", ".join([f"<{uri}>" for uri in filter_uris])
        filter_clause = f"FILTER (?p NOT IN ({uri_list}))"

    query = f"""
        CONSTRUCT {{ ?s ?p ?o }} 
        WHERE {{ 
            GRAPH <{graph_iri}> {{ 
                ?s ?p ?o .
                {filter_clause}
            }}
        }}
        """
    headers = {"Accept": "text/turtle"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=60)
    response.raise_for_status()

    normalized_text = unicodedata.normalize('NFC', response.content.decode('utf-8'))

    g = Graph()
    g.parse(data=normalized_text, format="turtle")
    return g


# --- Main UI ---
if st.button("Run Comparison", type="primary"):
    if not all([st_endpoint, gdb_endpoint, st_graph_iri, gdb_graph_iri]):
        st.warning("Please ensure all endpoints and IRIs are filled out.")
    else:
        try:
            with st.spinner("Analyzing..."):
                g1 = fetch_clean_graph(st_endpoint, st_graph_iri, excluded_uris)
                g2 = fetch_clean_graph(gdb_endpoint, gdb_graph_iri, excluded_uris)

                col1, col2 = st.columns(2)
                col1.metric(f"{st_env} Triples", len(g1))
                col2.metric(f"{gdb_env} Triples", len(g2))

                iso1 = to_isomorphic(g1)
                iso2 = to_isomorphic(g2)

                if iso1 == iso2:
                    st.success("Graphs are Identical")
                    st.balloons()

                    # Success Download
                    all_triples = sorted([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in iso1])
                    st.download_button("Download Verified Data (.nt)", "\n".join(all_triples), file_name="verified.nt")

                else:
                    st.error("Data Mismatch Detected")
                    in_both, only_in_st, only_in_gdb = graph_diff(g1, g2)

                    t1, t2 = st.tabs([f"Only in {st_env}", f"Only in {gdb_env}"])

                    with t1:
                        diff_st = "\n".join([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in only_in_st])
                        st.code(diff_st)
                        if len(only_in_st) > 0:
                            st.download_button(f"Download {st_env} unique triples", diff_st, file_name="st_diff.nt")

                    with t2:
                        diff_gdb = "\n".join([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in only_in_gdb])
                        st.code(diff_gdb)
                        if len(only_in_gdb) > 0:
                            st.download_button(f"Download {gdb_env} unique triples", diff_gdb, file_name="gdb_diff.nt")

        except Exception as e:
            st.error(f"Error: {e}")