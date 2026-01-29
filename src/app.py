import streamlit as st
import yaml
import requests
import unicodedata
import pandas as pd
from rdflib import Graph
from rdflib.compare import to_isomorphic, graph_diff
from pathlib import Path
import os

# --- 1. Configuration & Setup ---
BASE_DIR = Path(__file__).resolve().parent
config_path = BASE_DIR / "presets.yaml"


@st.cache_data
def load_config():
    if not config_path.exists():
        st.error(f"Critical Error: File not found at {config_path}")
        st.stop()
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


try:
    st.set_page_config(page_title="RDF Sync Validator", page_icon="⚖️", layout="wide")
    config = load_config()
except Exception as e:
    st.error(f"App failed to start: {e}")
    st.stop()


# --- 2. Helper Functions (The Engine) ---

def discover_cubes(endpoint, graph_iri):
    """Returns a set of all Cube IRIs in a graph."""
    query = f"SELECT DISTINCT ?cube WHERE {{ GRAPH <{graph_iri}> {{ ?cube a <https://cube.link/Cube> . }} }}"
    headers = {"Accept": "application/sparql-results+json"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=60)
    response.raise_for_status()
    return {row['cube']['value'] for row in response.json()['results']['bindings']}


def discover_constraints(endpoint, graph_iri):
    """Finds all unique cube:Constraint IRIs."""
    query = f"""
    SELECT DISTINCT ?constraint WHERE {{
        GRAPH <{graph_iri}> {{
            ?cube <https://cube.link/observationConstraint> ?constraint .
        }}
    }}
    """
    headers = {"Accept": "application/sparql-results+json"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=60)
    response.raise_for_status()
    return {row['constraint']['value'] for row in response.json()['results']['bindings']}


def fetch_cube_metadata(endpoint, graph_iri, cube_iri):
    """Only fetches triples where the Cube is the subject."""
    query = f"CONSTRUCT {{ <{cube_iri}> ?p ?o . }} WHERE {{ GRAPH <{graph_iri}> {{ <{cube_iri}> ?p ?o . }} }}"
    headers = {"Accept": "application/n-triples"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=60)
    response.raise_for_status()
    g = Graph()
    g.parse(data=unicodedata.normalize('NFC', response.text), format="nt")
    return g


def fetch_cube_observations(endpoint, graph_iri, cube_iri):
    """Fetches all observations linked to a specific cube."""
    query = f"""
    CONSTRUCT {{ ?observation ?p ?o . }} 
    WHERE {{
        GRAPH <{graph_iri}> {{
            <{cube_iri}> <https://cube.link/observationSet> ?obsSet .
            ?obsSet <https://cube.link/observation> ?observation .
            ?observation ?p ?o .
        }}
    }}
    """
    headers = {"Accept": "application/n-triples"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=180)
    response.raise_for_status()
    g = Graph()
    g.parse(data=unicodedata.normalize('NFC', response.text), format="nt")
    return g


def fetch_constraint_subgraph(endpoint, graph_iri, constraint_iri):
    """Universal deep-fetch for nested blank nodes using the recursion trick."""
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
    g.parse(data=unicodedata.normalize('NFC', response.text), format="nt")
    return g


def fetch_full_graph(endpoint, graph_iri, filter_uris):
    """Large scale fetch for non-cube-aware comparisons."""
    filter_clause = ""
    if filter_uris:
        uri_list = ", ".join([f"<{uri}>" for uri in filter_uris])
        filter_clause = f"FILTER (?p NOT IN ({uri_list}))"
    query = f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o . {filter_clause} }} }}"
    headers = {"Accept": "application/n-triples"}
    response = requests.get(endpoint, params={"query": query}, headers=headers, timeout=300)
    response.raise_for_status()
    g = Graph()
    g.parse(data=unicodedata.normalize('NFC', response.text), format="nt")
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
    selected_labels = st.multiselect("Exclude Predicates", list(config['filter_definitions'].keys()),
                                     default=current_preset.get('default_filters', []))
    excluded_uris = [config['filter_definitions'][label] for label in selected_labels]

# --- 4. Main UI Logic ---
st.title("⚖️ RDF Sync Validator")
st.caption(f"Comparing `{st_env}` ↔ `{gdb_env}`")

full_run = st.button("Run Full Graph Comparison", use_container_width=True)

st.divider()
st.subheader("Cube-wise Comparison")
st.info("Validate the Three Pillars of Cube.link. Blank nodes are handled automatically in 'Constraints'.")

col1, col2, col3 = st.columns(3)
with col1: meta_run = st.button("cube:Cube", type="primary", use_container_width=True)
with col2: obs_run = st.button("cube:Observation", type="primary", use_container_width=True)
with col3: const_run = st.button("cube:Constraint", type="primary", use_container_width=True)


# Shared Loop logic
def run_validation(mode_name, discovery_func, fetch_func):
    try:
        with st.status(f"Processing {mode_name}...", expanded=True) as status:
            st_items = discovery_func(st_endpoint, st_graph_iri)
            gdb_items = discovery_func(gdb_endpoint, gdb_graph_iri)

            # --- Debug Expander ---
            with st.expander("🔍 Debug: View IRIs"):
                cl1, cl2 = st.columns(2)
                cl1.write(f"Stardog ({len(st_items)})")
                cl1.write(sorted(list(st_items)))
                cl2.write(f"GraphDB ({len(gdb_items)})")
                cl2.write(sorted(list(gdb_items)))

            shared = sorted(list(st_items.intersection(gdb_items)))
            if not shared:
                status.update(label="❌ No matches found.", state="error")
                return

            results = []
            prog = st.progress(0)
            for i, iri in enumerate(shared):
                status.update(label=f"Comparing {i + 1}/{len(shared)}: {iri.split('/')[-1]}")
                g1 = fetch_func(st_endpoint, st_graph_iri, iri)
                g2 = fetch_func(gdb_endpoint, gdb_graph_iri, iri)

                match = (to_isomorphic(g1) == to_isomorphic(g2))
                results.append({"IRI": iri, "Match": match, "Triples": len(g1)})
                prog.progress((i + 1) / len(shared))

            status.update(label=f"✅ {mode_name} Complete", state="complete")

        df = pd.DataFrame(results)
        st.dataframe(df.style.map(lambda x: 'color: red' if x is False else 'color: green', subset=['Match']),
                     use_container_width=True)

        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Report", csv, f"{mode_name}_report.csv", "text/csv")

    except Exception as e:
        st.error(f"Failed: {e}")
        st.exception(e)


if meta_run: run_validation("Metadata", discover_cubes, fetch_cube_metadata)
if obs_run: run_validation("Observations", discover_cubes, fetch_cube_observations)
if const_run: run_validation("Constraints", discover_constraints, fetch_constraint_subgraph)

if full_run:
    # Full Run Logic (Option B)
    try:
        with st.spinner("Executing global graph fetch..."):
            g1 = fetch_full_graph(st_endpoint, st_graph_iri, excluded_uris)
            g2 = fetch_full_graph(gdb_endpoint, gdb_graph_iri, excluded_uris)

            if to_isomorphic(g1) == to_isomorphic(g2):
                st.success("Graphs are Identical")
            else:
                st.error("Mismatch Found")
                _, only_st, only_gdb = graph_diff(g1, g2)
                t1, t2 = st.tabs([f"Only in {st_env} ({len(only_st)})", f"Only in {gdb_env} ({len(only_gdb)})"])

                with t1:
                    if len(only_st) > 0:
                        diff_st = "\n".join([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in only_st])
                        st.code(diff_st)
                        st.download_button(f"Download {st_env} diff", diff_st, file_name="st_diff.nt")
                    else:
                        st.write("No unique triples.")

                with t2:
                    if len(only_gdb) > 0:
                        diff_gdb = "\n".join([f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in only_gdb])
                        st.code(diff_gdb)
                        st.download_button(f"Download {gdb_env} diff", diff_gdb, file_name="gdb_diff.nt")
                    else:
                        st.write("No unique triples.")

                st.write(f"Only in Stardog: {len(only_st)} | Only in GraphDB: {len(only_gdb)}")
    except Exception as e:
        st.exception(e)