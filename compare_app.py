import streamlit as st
import requests
import unicodedata
from rdflib import Graph
from rdflib.compare import to_isomorphic, graph_diff

# Define Known Endpoints
ENDPOINTS_STARDOG = {
    "LINDAS PROD": "https://lindas.admin.ch/query",
    "LINDAS INT": "https://int.lindas.admin.ch/query"
}
ENDPOINTS_GRAPHDB = {
    "LINDASnext PROD": "https://lindas.cz-aws.net/query",
    "LINDASnext INT": "https://lindas.int.cz-aws.net/query"
}

# Define Dataset Presets
PRESETS = {
    "Forest Fire Prevention": {
        "st_graph": "https://lindas.admin.ch/foen/gefahren-waldbrand-praeventionsmassnahmen-kantone",
        "gdb_graph": "https://lindas.admin.ch/foen/forest-fire-prevention-measures-cantons",
    },
    "Custom (Manual Input)": {
        "st_graph": "",
        "gdb_graph": "",
    }
}

COMMON_FILTERS = {
    "DCAT: dateModified": "http://www.w3.org/ns/dcat#dateModified",
    "DCTERMS: modified": "http://purl.org/dc/terms/modified"
}

st.set_page_config(page_title="RDF Sync Validator", page_icon="⚖️", layout="wide")

st.title("LINDASnext RDF Sync Validator")

# --- Sidebar Configuration ---
with st.sidebar:
    st.header("1. Environments")

    st_env = st.selectbox("Stardog Instance", list(ENDPOINTS_STARDOG.keys()))
    st_endpoint = ENDPOINTS_STARDOG[st_env]
    st.caption(f"Connected to: `{st_endpoint}`")

    gdb_env = st.selectbox("GraphDB Instance", list(ENDPOINTS_GRAPHDB.keys()))
    gdb_endpoint = ENDPOINTS_GRAPHDB[gdb_env]
    st.caption(f"Connected to: `{gdb_endpoint}`")

    st.divider()

    st.header("2. Datasets")
    selection = st.selectbox("Select Dataset", list(PRESETS.keys()))
    preset = PRESETS[selection]

    st_graph_iri = st.text_input("Stardog Graph IRI", value=preset["st_graph"])
    gdb_graph_iri = st.text_input("GraphDB Graph IRI", value=preset["gdb_graph"])

    st.divider()

    st.header("3. Filter Settings")
    selected_filter_labels = st.multiselect(
        "Exclude Predicates",
        options=list(COMMON_FILTERS.keys()),
        default=["DCAT: dateModified"]
    )

    # We use a text_area or text_input for multiple custom URIs
    custom_filters_raw = st.text_area("Additional URIs to exclude (one per line)")

    # 1. Start with the pre-selected URIs
    excluded_uris = [COMMON_FILTERS[label] for label in selected_filter_labels]

    # 2. Add custom ones, filtering out empty lines
    if custom_filters_raw:
        custom_list = [line.strip() for line in custom_filters_raw.split("\n") if line.strip()]
        excluded_uris.extend(custom_list)


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
                    st.success("✅ Graphs are Identical")
                    st.balloons()

                    # Success Download
                    all_triples = sorted([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in iso1])
                    st.download_button("Download Verified Data (.nt)", "\n".join(all_triples), file_name="verified.nt")

                else:
                    st.error("❌ Data Mismatch Detected")
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