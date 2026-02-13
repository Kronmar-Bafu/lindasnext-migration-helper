import streamlit as st
import yaml
import requests
import unicodedata
import pandas as pd
from rdflib import Graph, Literal
from rdflib.compare import to_isomorphic, graph_diff
from pathlib import Path
import random
import io

# --- 1. Configuration & Setup ---
BASE_DIR = Path(__file__).resolve().parent.parent
config_path = BASE_DIR / "presets.yaml"


@st.cache_data
def load_config():
    if not config_path.exists():
        st.error(f"Critical Error: File not found at {config_path}")
        st.stop()
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

try:
    config = load_config()
except Exception as e:
    st.error(f"Failed to load config: {e}")
    st.stop()


# --- 2. Helper Functions (The Engine) ---

def discover_items(endpoint, graph_iri, rdf_type):
    """Generic discovery for IRIs of a specific type. Fetches ALL to ensure population sync."""
    query = f"SELECT DISTINCT ?item WHERE {{ GRAPH <{graph_iri}> {{ ?item a <{rdf_type}> . }} }}"
    headers = {"Accept": "application/sparql-results+json"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=120)
    response.raise_for_status()
    return {row['item']['value'] for row in response.json()['results']['bindings']}


def fetch_cube_metadata(endpoint, graph_iri, cube_iri, filter_uris=None):
    filter_clause = ""
    if filter_uris:
        uri_list = ", ".join([f"<{uri}>" for uri in filter_uris])
        filter_clause = f"FILTER (?p NOT IN ({uri_list}))"

    query = f"CONSTRUCT {{ <{cube_iri}> ?p ?o . }} WHERE {{ GRAPH <{graph_iri}> {{ <{cube_iri}> ?p ?o . {filter_clause} }} }}"
    headers = {"Accept": "application/n-triples"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=60)
    response.raise_for_status()
    g = Graph()
    g.parse(data=response.text, format="nt")  # Parse raw first
    g = normalize_graph_literals(g)           # Then normalize objects
    return g


def normalize_graph_literals(graph):
    for s, p, o in graph:
        if isinstance(o, Literal):
            # Normalize the string content of the literal
            norm_val = unicodedata.normalize('NFC', str(o))
            if str(o) != norm_val:
                graph.remove((s, p, o))
                graph.add((s, p, Literal(norm_val, lang=o.language, datatype=o.datatype)))
    return graph


def fetch_subject_triples(endpoint, graph_iri, iri):
    """Standard fetch for non-filtered subjects (Observations)."""
    query = f"CONSTRUCT {{ <{iri}> ?p ?o . }} WHERE {{ GRAPH <{graph_iri}> {{ <{iri}> ?p ?o . }} }}"
    headers = {"Accept": "application/n-triples"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=60)
    response.raise_for_status()
    g = Graph()
    g.parse(data=response.text, format="nt")  # Parse raw first
    g = normalize_graph_literals(g)           # Then normalize objects
    return g


def fetch_constraint_subgraph(endpoint, graph_iri, constraint_iri):
    query = f"""
    CONSTRUCT {{ 
        <{constraint_iri}> ?p ?o .
        ?bn ?p2 ?o2 .
    }} WHERE {{
        GRAPH <{graph_iri}> {{
            <{constraint_iri}> (!<http://nodefault>)* ?bn .
            ?bn ?p2 ?o2 .
            FILTER (ISBLANK(?bn) || ?bn = <{constraint_iri}>)
        }}
    }}
    """
    headers = {"Accept": "application/n-triples"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=120)
    response.raise_for_status()
    g = Graph()
    g.parse(data=response.text, format="nt")  # Parse raw first
    g = normalize_graph_literals(g)  # Then normalize objects
    return g


def fetch_full_graph(endpoint, graph_iri, filter_uris):
    filter_clause = ""
    if filter_uris:
        uri_list = ", ".join([f"<{uri}>" for uri in filter_uris])
        filter_clause = f"FILTER (?p NOT IN ({uri_list}))"
    query = f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o . {filter_clause} }} }}"
    headers = {"Accept": "application/n-triples"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=300)
    response.raise_for_status()
    g = Graph()
    g.parse(data=response.text, format="nt")  # Parse raw first
    g = normalize_graph_literals(g)  # Then normalize objects
    return g


# --- 3. Sidebar UI ---
with st.sidebar:
    st.header("1. Environments")
    st_env = st.selectbox("Stardog", [e['name'] for e in config['endpoints_stardog']])
    st_endpoint = next(e['url'] for e in config['endpoints_stardog'] if e['name'] == st_env)
    gdb_env = st.selectbox("GraphDB", [e['name'] for e in config['endpoints_graphdb']])
    gdb_endpoint = next(e['url'] for e in config['endpoints_graphdb'] if e['name'] == gdb_env)

    st.divider()
    st.header("2. Datasets")
    selection = st.selectbox("Preset", [p['name'] for p in config['presets']])
    current_preset = next(p for p in config['presets'] if p['name'] == selection)
    st_graph_iri = st.text_input("Stardog Graph", value=current_preset['st_graph'])
    gdb_graph_iri = st.text_input("GraphDB Graph", value=current_preset['gdb_graph'])

    st.header("3. Filters")
    selected_labels = st.multiselect("Exclude Metadata Predicates", list(config['filter_definitions'].keys()),
                                     default=current_preset.get('default_filters', []))
    excluded_uris = [config['filter_definitions'][label] for label in selected_labels]

    st.header("4. Sampling")
    sample_size = st.number_input("Max Triple-Checks", min_value=1, max_value=5000, value=100,
                                  help="How many IRIs from the shared list should be deeply compared?")

# --- 4. Main UI Logic ---
st.title("⚖️ RDF Sync Validator")
st.caption(f"Comparing `{st_env}` ↔ `{gdb_env}`")

full_run = st.button("Run Full Graph Comparison", use_container_width=True)
st.divider()
st.subheader("Component-wise Comparison")

col1, col2, col3 = st.columns(3)
with col1: meta_run = st.button("cube:Cube", type="primary", use_container_width=True)
with col2: obs_run = st.button("cube:Observation", type="primary", use_container_width=True)
with col3: const_run = st.button("cube:Constraint", type="primary", use_container_width=True)


def run_validation(mode_name, rdf_type, fetch_func, filters=None, use_sampling=False):
    try:
        with st.status(f"Validating {mode_name} population...", expanded=True) as status:
            # Step 1: Discover ALL IRIs
            st_items = discover_items(st_endpoint, st_graph_iri, rdf_type)
            gdb_items = discover_items(gdb_endpoint, gdb_graph_iri, rdf_type)

            # Step 2: Compare populations
            shared = st_items.intersection(gdb_items)
            only_st = st_items - gdb_items
            only_gdb = gdb_items - st_items

            if only_st or only_gdb:
                st.warning(
                    f"Population Mismatch: {len(only_st)} unique to {st_env}, {len(only_gdb)} unique to {gdb_env}.")
                with st.expander("View Population Discrepancies"):
                    c1, c2 = st.columns(2)
                    c1.write(f"Only in {st_env}")
                    c1.write(list(only_st)[:100])
                    c2.write(f"Only in {gdb_env}")
                    c2.write(list(only_gdb)[:100])
            else:
                st.success(f"Population Match: Both endpoints contain the same {len(shared)} {mode_name} IRIs.")

            if not shared:
                status.update(label="❌ No shared IRIs found. Aborting deep-check.", state="error")
                return

            # Step 3: Sampling
            items_to_check = sorted(list(shared))
            if use_sampling and len(items_to_check) > sample_size:
                st.info(f"Sampling {sample_size} out of {len(items_to_check)} shared items for triple-level check.")
                items_to_check = random.sample(items_to_check, sample_size)

            # Step 4: Deep Triple-level Comparison
            results = []
            all_st_graph, all_gdb_graph = Graph(), Graph()
            prog = st.progress(0)

            for i, iri in enumerate(items_to_check):
                status.update(label=f"Checking Triples {i + 1}/{len(items_to_check)}: {iri.split('/')[-1]}")

                # Fetch
                if mode_name == "Metadata" and filters:
                    g1 = fetch_func(st_endpoint, st_graph_iri, iri, filters)
                    g2 = fetch_func(gdb_endpoint, gdb_graph_iri, iri, filters)
                else:
                    g1 = fetch_func(st_endpoint, st_graph_iri, iri)
                    g2 = fetch_func(gdb_endpoint, gdb_graph_iri, iri)

                all_st_graph += g1
                all_gdb_graph += g2

                match = (to_isomorphic(g1) == to_isomorphic(g2))
                results.append({"IRI": iri, "Match": match, "Triples": len(g1)})
                prog.progress((i + 1) / len(items_to_check))

            status.update(label=f"✅ {mode_name} Comparison Complete", state="complete")

        # Report
        df = pd.DataFrame(results)
        st.dataframe(df.style.map(lambda x: 'color: red' if x is False else 'color: green', subset=['Match']),
                     use_container_width=True)

        st.markdown(f"### 📥 Export {mode_name} Sample Data")
        c1, c2, c3 = st.columns(3)
        c1.download_button("📊 CSV Report", df.to_csv(index=False).encode('utf-8'), f"{mode_name}_report.csv",
                           "text/csv", use_container_width=True)
        c2.download_button(f"📦 {st_env} Sample (.nt)", all_st_graph.serialize(format="nt"), f"{mode_name}_st.nt",
                           "text/plain", use_container_width=True)
        c3.download_button(f"📦 {gdb_env} Sample (.nt)", all_gdb_graph.serialize(format="nt"), f"{mode_name}_gdb.nt",
                           "text/plain", use_container_width=True)

    except Exception as e:
        st.error(f"Error: {e}")


# --- Triggers ---
if meta_run:
    run_validation("Metadata", "https://cube.link/Cube", fetch_cube_metadata, filters=excluded_uris, use_sampling=False)

if obs_run:
    run_validation("Observations", "https://cube.link/Observation", fetch_subject_triples, use_sampling=True)

if const_run:
    # Usually small enough to check all, but sampling is available
    run_validation("Constraints", "https://cube.link/Constraint", fetch_constraint_subgraph, use_sampling=False)

if full_run:
    try:
        with st.spinner("Fetching full graphs..."):
            g1 = fetch_full_graph(st_endpoint, st_graph_iri, excluded_uris)
            g2 = fetch_full_graph(gdb_endpoint, gdb_graph_iri, excluded_uris)
            if to_isomorphic(g1) == to_isomorphic(g2):
                st.success("Graphs are Identical")
            else:
                st.error("Mismatch Found")
                _, only_st, only_gdb = graph_diff(g1, g2)
                t1, t2 = st.tabs([f"Only in {st_env}", f"Only in {gdb_env}"])
                with t1:
                    st.code("\n".join([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in only_st]))
                with t2:
                    st.code("\n".join([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in only_gdb]))
    except Exception as e:
        st.exception(e)