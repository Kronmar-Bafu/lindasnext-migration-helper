import requests
import unicodedata
from rdflib import Graph
from rdflib.compare import to_isomorphic, graph_diff


def fetch_and_normalize(endpoint, graph_iri):
    print(f"Fetching and normalizing: {graph_iri}")
    query = f"""
    CONSTRUCT {{ ?s ?p ?o }} 
    WHERE {{ 
        GRAPH <{graph_iri}> {{ 
            ?s ?p ?o .
            FILTER (?p != <http://schema.org/dateModified>)
            FILTER (?p != <http://purl.org/dc/terms/modified>)
        }}
    }}
    """
    # 1. Use a standard format like Turtle or N-Triples
    headers = {"Accept": "text/turtle"}
    response = requests.get(endpoint, params={"query": query}, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Error {response.status_code}: {response.text}")

    # 2. Decode raw bytes to string and normalize Unicode (NFC)
    # This fixes issues where 'ü' is represented differently across systems
    raw_text = response.content.decode('utf-8')
    normalized_text = unicodedata.normalize('NFC', raw_text)

    g = Graph()
    g.parse(data=normalized_text, format="turtle")
    return g


# Configuration
STARDOG_EP = "https://lindas.admin.ch/query"
GRAPHDB_EP = "https://lindas.cz-aws.net/query"  # Update if the other instance has a different URL

GRAPH_GRAPHDB = "https://lindas.admin.ch/foen/forest-fire-prevention-measures-cantons"
GRAPH_STARDOG = "https://lindas.admin.ch/foen/gefahren-waldbrand-praeventionsmassnahmen-kantone"

try:
    # 1. Load data
    g_stardog = fetch_and_normalize(STARDOG_EP, GRAPH_STARDOG)
    g_graphdb = fetch_and_normalize(GRAPHDB_EP, GRAPH_GRAPHDB)

    print(f"Loaded {len(g_stardog)} triples from Graph 1.")
    print(f"Loaded {len(g_graphdb)} triples from Graph 2.")

    # 2. Compare
    iso1 = to_isomorphic(g_stardog)
    iso2 = to_isomorphic(g_graphdb)

    if iso1 == iso2:
        print("\n✅ SUCCESS: The datasets are identical.")
    else:
        print("\n❌ DATA MISMATCH: Finding differences...")
        # 3. Show exactly what is different
        in_both, only_in_g_stardog, only_in_g_graphdb = graph_diff(g_stardog, g_graphdb)

        if len(only_in_g_stardog) > 0:
            print(f"\nTriples only in Graph 1 ({len(only_in_g_stardog)}):")
            for s, p, o in list(only_in_g_stardog)[:5]:  # Show first 5
                print(f"  {s} {p} {o}")
            only_in_g_stardog.serialize("only_in_stardog.ttl")

        if len(only_in_g_graphdb) > 0:
            print(f"\nTriples only in Graph 2 ({len(only_in_g_graphdb)}):")
            for s, p, o in list(only_in_g_graphdb)[:5]:
                print(f"  {s} {p} {o}")
            only_in_g_graphdb.serialize(("only_in_graphdb.ttl"))

except Exception as e:
    print(f"Error during execution: {e}")