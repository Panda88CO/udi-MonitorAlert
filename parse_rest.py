import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth

# --- CONFIGURATION ---
ISY_IP = "192.168.1.204"  # Replace with your Polisy / eisy IP
USERNAME = "christian.olgaard@gmail.com"
PASSWORD = "coe123COE"

BASE_URL = f"http://{ISY_IP}/rest"
AUTH = HTTPBasicAuth(USERNAME, PASSWORD)

# Global caches so we only download profile assets once per slot
PROFILE_CACHE = {}  # Format: { profile_id: { "node_defs": {...}, "nls": {...} } }


def fetch_xml(url):
    """Utility to fetch and parse XML."""
    try:
        response = requests.get(url, auth=AUTH, timeout=10)
        response.raise_for_status()
        return ET.fromstring(response.content)
    except requests.exceptions.RequestException as e:
        print(f"  [Error] Failed XML fetch for {url}: {e}")
        return None


def fetch_nls(url):
    """Utility to fetch and parse text-based NLS files."""
    try:
        response = requests.get(url, auth=AUTH, timeout=10)
        response.raise_for_status()
        nls_dict = {}
        for line in response.text.splitlines():
            if line.startswith("#") or not line.strip() or "=" not in line:
                continue
            key, val = line.split("=", 1)
            nls_dict[key.strip()] = val.strip()
        return nls_dict
    except Exception as e:
        # Some core profile paths or empty slots might not have NLS files, fail gracefully
        return {}


def load_profile_metadata(profile_id):
    """Downloads and compiles profile schemas for a specific node server slot if not cached."""
    if profile_id in PROFILE_CACHE:
        return PROFILE_CACHE[profile_id]

    print(f"--> Loading and caching definitions for Profile Slot [{profile_id}]...")
    nodedefs_url = f"{BASE_URL}/ns/{profile_id}/profile/nodedefs"
    editors_url = f"{BASE_URL}/ns/{profile_id}/profile/editors"
    nls_url = f"{BASE_URL}/ns/{profile_id}/profile/nls"

    nodedefs_xml = fetch_xml(nodedefs_url)
    editors_xml = fetch_xml(editors_url)
    nls_data = fetch_nls(nls_url)

    # Parse Editors
    editors = {}
    if editors_xml is not None:
        for editor in editors_xml.findall(".//editor"):
            ed_id = editor.attrib.get("id")
            editors[ed_id] = {"range": []}
            for r in editor.findall(".//range"):
                editors[ed_id]["range"].append({
                    "uom": r.attrib.get("uom"),
                    "subset": r.attrib.get("subset"),
                    "nls": r.attrib.get("nls")
                })

    # Parse Node Definitions
    node_defs = {}
    if nodedefs_xml is not None:
        for node_def in nodedefs_xml.findall(".//nodeDef"):
            ndef_id = node_def.attrib.get("id")
            node_defs[ndef_id] = {"drivers": {}}
            for st in node_def.findall(".//sts/st"):
                st_id = st.attrib.get("id")
                node_defs[ndef_id]["drivers"][st_id] = {
                    "editor_id": st.attrib.get("editor"),
                    "editor_info": editors.get(st.attrib.get("editor"), {})
                }

    # Save to global cache
    PROFILE_CACHE[profile_id] = {
        "node_defs": node_defs,
        "nls": nls_data
    }
    return PROFILE_CACHE[profile_id]


def parse_all_registered_nodes():
    """Queries the entire loop of registered nodes and parses them using dynamic profiles."""
    print("Requesting global master node list...")
    nodes_xml = fetch_xml(f"{BASE_URL}/nodes")
    
    if nodes_xml is None:
        print("Failed to retrieve nodes.")
        return

    all_nodes = nodes_xml.findall(".//node")
    print(f"Found {len(all_nodes)} total registered nodes configuration. Processing...\n")

    for node in all_nodes:
        node_address = node.find("address").text if node.find("address") is not None else "Unknown"
        node_name = node.find("name").text if node.find("name") is not None else "Unknown"
        node_def_id = node.attrib.get("nodeDefId")
        
        # 'profile' tells us which slot profile houses this node's definitions
        profile_id = node.attrib.get("profile")

        # If profile missing, it might be an internal system node (e.g. ID 'ZY000' for Z-Wave controllers)
        if not profile_id:
            continue

        # Dynamically load/fetch the definition matrices for this node's specific profile context
        meta = load_profile_metadata(profile_id)
        node_defs = meta["node_defs"]
        nls_data = meta["nls"]

        print(f"\n==================================================")
        print(f"NODE: {node_name} [{node_address}]")
        print(f"Profile Slot: {profile_id} | Definition ID: {node_def_id}")
        print(f"--------------------------------------------------")

        # Match active properties against the calculated schema definitions
        def_meta = node_defs.get(node_def_id)
        
        for prop in node.findall(".//property"):
            prop_id = prop.attrib.get("id")              # e.g., 'ST', 'GV1'
            raw_val = prop.attrib.get("value")           # e.g., '20'
            formatted = prop.attrib.get("formatted")     # e.g., '20°C' or 'On'

            # Lookup property friendly name in NLS (e.g., D-ST = "Status")
            nls_driver_key = f"D-{prop_id}"
            driver_label = nls_data.get(nls_driver_key, prop_id)

            print(f"  -> {driver_label} ({prop_id}): {formatted} (Raw: {raw_val})")

            # Deep parse editor data if it exists for this property
            if def_meta and prop_id in def_meta["drivers"]:
                driver_meta = def_meta["drivers"][prop_id]
                for r in driver_meta["editor_info"].get("range", []):
                    nls_subset_prefix = r.get("nls")
                    if nls_subset_prefix:
                        # Attempt to explicitly map state value translations
                        nls_lookup_key = f"{nls_subset_prefix}_{raw_val}"
                        if nls_lookup_key in nls_data:
                            print(f"     NLS Definition Mapping: {nls_data[nls_lookup_key]}")


if __name__ == "__main__":
    parse_all_registered_nodes()