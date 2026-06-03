import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth
import re

# --- CONFIGURATION ---
ISY_IP = "192.168.1.204"  # Replace with your Polisy / eisy IP
USERNAME = "christian.olgaard@gmail.com"
PASSWORD = "coe123COE"

BASE_URL = f"http://{ISY_IP}/rest"
AUTH = HTTPBasicAuth(USERNAME, PASSWORD)

# Global caches so we only download profile assets once per slot
PROFILE_CACHE = {}  # Format: { profile_id: { "node_defs": {...}, "nls": {...} } }

UOM_LABELS = {
    4: "Celsius",
    17: "Fahrenheit",
    25: "Index",
    35: "Liter",
    44: "Minute",
    51: "Percent",
    57: "Second",
    70: "Count",
    73: "Watt",
    119: "WattHour",
    151: "UnixTime",
}


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


def _coerce_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fetch_text(url, auth, timeout=10):
    try:
        response = requests.get(url, auth=auth, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException:
        return None


def _fetch_xml_with_auth(url, auth, timeout=10):
    try:
        response = requests.get(url, auth=auth, timeout=timeout)
        response.raise_for_status()
        return ET.fromstring(response.content)
    except (requests.exceptions.RequestException, ET.ParseError):
        return None


def _parse_nls_text(raw_text):
    out = {}
    if not raw_text:
        return out
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def _parse_subset_values(subset):
    if not subset:
        return None
    values = set()
    for token in str(subset).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            try:
                start_s, end_s = token.split("-", 1)
                start_i = int(start_s)
                end_i = int(end_s)
            except ValueError:
                continue
            step = 1 if end_i >= start_i else -1
            for val in range(start_i, end_i + step, step):
                values.add(str(val))
            continue
        values.add(token)
    return values


def _slot_from_node_id(node_id):
    if not node_id:
        return None
    match = re.match(r"n(\d+)_", str(node_id))
    if not match:
        return None
    # Normalize slot id (n008 -> 8).
    return str(int(match.group(1)))


def _load_profile_assets(rest_base_url, auth, slot):
    cache_key = f"slot::{slot}"
    if cache_key in PROFILE_CACHE:
        return PROFILE_CACHE[cache_key]

    files_xml = _fetch_xml_with_auth(f"{rest_base_url}/profiles/ns/{slot}/files", auth=auth)

    file_names: dict[str, str | None] = {
        "nodedef": None,
        "editor": None,
        "nls": None,
    }
    if files_xml is not None:
        for files_block in files_xml.findall(".//files"):
            folder = files_block.attrib.get("dir")
            first_file = files_block.find("file")
            if folder in file_names and first_file is not None:
                name = first_file.attrib.get("name")
                if name:
                    file_names[folder] = name

    nodedef_name = file_names["nodedef"] or "nodedefs.xml"
    editor_name = file_names["editor"] or "editors.xml"
    nls_name = file_names["nls"] or "en_us.txt"

    nodedef_xml = _fetch_xml_with_auth(
        f"{rest_base_url}/profiles/ns/{slot}/download/nodedef/{nodedef_name}",
        auth=auth,
    )
    editors_xml = _fetch_xml_with_auth(
        f"{rest_base_url}/profiles/ns/{slot}/download/editor/{editor_name}",
        auth=auth,
    )
    nls_text = _fetch_text(
        f"{rest_base_url}/profiles/ns/{slot}/download/nls/{nls_name}",
        auth=auth,
    )
    nls_map = _parse_nls_text(nls_text)

    editors = {}
    if editors_xml is not None:
        for editor in editors_xml.findall(".//editor"):
            editor_id = editor.attrib.get("id")
            if not editor_id:
                continue
            ranges = []
            for rng in editor.findall(".//range"):
                ranges.append({
                    "uom": _coerce_int(rng.attrib.get("uom")),
                    "subset": _parse_subset_values(rng.attrib.get("subset")),
                    "nls": rng.attrib.get("nls"),
                })
            editors[editor_id] = ranges

    # Build a control -> candidate editor range list map for this slot.
    control_candidates = {}
    if nodedef_xml is not None:
        for st in nodedef_xml.findall(".//nodeDef/sts/st"):
            control = st.attrib.get("id")
            editor_id = st.attrib.get("editor")
            if not control or not editor_id:
                continue
            ranges = editors.get(editor_id, [])
            if not ranges:
                continue
            control_candidates.setdefault(control, [])
            for rng in ranges:
                control_candidates[control].append({
                    "editor_id": editor_id,
                    "uom": rng.get("uom"),
                    "subset": rng.get("subset"),
                    "nls": rng.get("nls"),
                })

    out = {
        "slot": slot,
        "control_candidates": control_candidates,
        "nls": nls_map,
    }
    PROFILE_CACHE[cache_key] = out
    return out


def _resolve_uom25_enum_text(slot_assets, control, value_str, fallback_text):
    if not slot_assets:
        return fallback_text

    candidates = slot_assets.get("control_candidates", {}).get(control, [])
    nls_map = slot_assets.get("nls", {})
    if not candidates or not nls_map:
        return fallback_text

    resolved = set()
    for candidate in candidates:
        if candidate.get("uom") != 25:
            continue
        subset = candidate.get("subset")
        if subset is not None and value_str not in subset:
            continue
        nls_prefix = candidate.get("nls")
        if not nls_prefix:
            continue
        text = nls_map.get(f"{nls_prefix}-{value_str}")
        if text:
            resolved.add(text)

    if len(resolved) == 1:
        return next(iter(resolved))
    return fallback_text


def build_control_metadata_records(rest_base_url, username, password, timeout=10):
    """Builds metadata records keyed by node/control from /rest/status and profile files.

    Returns a tuple: (records, stats)
    records: list[dict] ready for database.upsert_static_metadata(**record)
    """
    auth = HTTPBasicAuth(username, password)
    status_xml = _fetch_xml_with_auth(f"{rest_base_url}/status", auth=auth, timeout=timeout)
    if status_xml is None:
        return [], {"status_nodes": 0, "status_properties": 0, "slots_loaded": 0, "records": 0}

    records = []
    loaded_slots = set()
    status_nodes = 0
    status_props = 0

    for node in status_xml.findall(".//node"):
        node_id = node.attrib.get("id")
        if not node_id:
            continue
        status_nodes += 1

        slot = node.attrib.get("profile") or _slot_from_node_id(node_id)
        slot_assets = None
        if slot:
            slot_assets = _load_profile_assets(rest_base_url, auth, slot)
            loaded_slots.add(slot)

        for prop in node.findall("property"):
            control = prop.attrib.get("id")
            if not control:
                continue
            status_props += 1

            uom = _coerce_int(prop.attrib.get("uom"))
            value_raw = prop.attrib.get("value")
            value_int = _coerce_int(value_raw)
            formatted = prop.attrib.get("formatted")

            enum_text = None
            if uom == 25 and value_raw is not None:
                enum_text = _resolve_uom25_enum_text(
                    slot_assets=slot_assets,
                    control=control,
                    value_str=str(value_raw),
                    fallback_text=formatted,
                )

            records.append({
                "node_id": str(node_id),
                "control": str(control),
                "uom": uom,
                "uom_label": UOM_LABELS.get(uom) if uom is not None else None,
                "source": "status_profile" if slot else "status_internal",
                "enum_value": value_int if uom == 25 else None,
                "enum_text": enum_text,
            })

    stats = {
        "status_nodes": status_nodes,
        "status_properties": status_props,
        "slots_loaded": len(loaded_slots),
        "records": len(records),
    }
    return records, stats


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
        address_el = node.find("address")
        name_el = node.find("name")
        node_address = address_el.text if (address_el is not None and address_el.text) else "Unknown"
        node_name = name_el.text if (name_el is not None and name_el.text) else "Unknown"
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