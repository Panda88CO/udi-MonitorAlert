import xml.etree.ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth
import re
from collections import OrderedDict

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


def _coerce_float(value):
    try:
        return float(value)
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


def _candidate_matches_value(candidate, uom, value_str):
    if candidate.get("uom") != uom:
        return False

    subset = candidate.get("subset")
    if subset is not None:
        return value_str in subset

    value_num = _coerce_float(value_str)
    min_value = candidate.get("min")
    max_value = candidate.get("max")
    if value_num is None:
        return True
    if min_value is not None and value_num < min_value:
        return False
    if max_value is not None and value_num > max_value:
        return False
    return True


def _select_candidate(slot_assets, control, uom, value_str):
    if not slot_assets:
        return None
    candidates = slot_assets.get("control_candidates", {}).get(control, [])
    if not candidates:
        return None

    exact_matches = [c for c in candidates if _candidate_matches_value(c, uom, value_str)]
    if exact_matches:
        return exact_matches[0]

    same_uom = [c for c in candidates if c.get("uom") == uom]
    if same_uom:
        return same_uom[0]
    return None


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
                    "min": _coerce_float(rng.attrib.get("min")),
                    "max": _coerce_float(rng.attrib.get("max")),
                })
            editors[editor_id] = ranges

    # Build a control -> candidate editor range list map for this slot.
    control_candidates = {}
    node_defs = {}
    if nodedef_xml is not None:
        for node_def in nodedef_xml.findall(".//nodeDef"):
            node_def_id = node_def.attrib.get("id")
            if not node_def_id:
                continue
            node_defs[node_def_id] = {"controls": {}}

            for st in node_def.findall(".//sts/st"):
                control = st.attrib.get("id")
                editor_id = st.attrib.get("editor")
                if not control or not editor_id:
                    continue
                ranges = editors.get(editor_id, [])
                if not ranges:
                    continue
                node_defs[node_def_id]["controls"].setdefault(control, [])
                control_candidates.setdefault(control, [])
                for range_index, rng in enumerate(ranges):
                    candidate = {
                        "node_def_id": node_def_id,
                        "control": control,
                        "editor_id": editor_id,
                        "range_index": range_index,
                        "uom": rng.get("uom"),
                        "subset": rng.get("subset"),
                        "nls": rng.get("nls"),
                        "min": rng.get("min"),
                        "max": rng.get("max"),
                    }
                    node_defs[node_def_id]["controls"][control].append(candidate)
                    control_candidates[control].append(candidate)

    out = {
        "slot": slot,
        "node_defs": node_defs,
        "control_candidates": control_candidates,
        "nls": nls_map,
    }
    PROFILE_CACHE[cache_key] = out
    return out


def _extract_profile_slots(profiles_xml):
    slots = set()
    if profiles_xml is None:
        return slots

    for element in profiles_xml.iter():
        for raw in element.attrib.values():
            text = str(raw)
            for match in re.findall(r"/profiles/ns/(\d+)", text):
                slots.add(str(int(match)))

        tag_name = str(element.tag).lower()
        if tag_name.endswith("profile") or tag_name.endswith("slot") or tag_name.endswith("id"):
            raw_text = (element.text or "").strip()
            if raw_text.isdigit():
                slots.add(str(int(raw_text)))

    return slots


def _candidate_enum_map(slot_assets, candidate):
    if not slot_assets or not isinstance(candidate, dict):
        return None
    if candidate.get("uom") != 25:
        return None

    nls_prefix = candidate.get("nls")
    subset = candidate.get("subset")
    nls_map = slot_assets.get("nls", {})
    if not nls_prefix or subset is None or not nls_map:
        return None

    enum_map = OrderedDict()
    for raw_value in sorted({str(v) for v in subset}):
        text = nls_map.get(f"{nls_prefix}-{raw_value}")
        if text:
            enum_map[raw_value] = text
    return dict(enum_map) if enum_map else None


def build_profile_catalog_records(rest_base_url, username, password, timeout=10):
    """Build static profile schema rows from /rest/profiles assets.

    Returns a tuple: (records, stats)
    records: list[dict] ready for database.bulk_upsert_profile_control_schema(records)
    """
    auth = HTTPBasicAuth(username, password)
    profiles_xml = _fetch_xml_with_auth(f"{rest_base_url}/profiles", auth=auth, timeout=timeout)
    if profiles_xml is None:
        return [], {"profile_slots": 0, "node_defs": 0, "controls": 0, "records": 0}

    slots = sorted(_extract_profile_slots(profiles_xml), key=lambda v: int(v))
    records = []
    node_def_count = 0
    control_count = 0

    for slot in slots:
        slot_assets = _load_profile_assets(rest_base_url, auth, slot)
        if not slot_assets:
            continue

        node_defs = slot_assets.get("node_defs", {})
        node_def_count += len(node_defs)

        for node_def_id, node_def_meta in node_defs.items():
            controls = node_def_meta.get("controls", {})
            control_count += len(controls)
            for control, candidates in controls.items():
                for candidate in candidates:
                    allowed_subset = None
                    subset = candidate.get("subset")
                    if subset is not None:
                        allowed_subset = sorted({str(v) for v in subset})

                    uom = _coerce_int(candidate.get("uom"))
                    records.append({
                        "profile_slot": str(slot),
                        "node_def_id": str(node_def_id),
                        "control": str(control),
                        "editor_id": candidate.get("editor_id"),
                        "range_index": _coerce_int(candidate.get("range_index")) or 0,
                        "uom": uom,
                        "uom_label": UOM_LABELS.get(uom) if uom is not None else None,
                        "nls_prefix": candidate.get("nls"),
                        "min_value": candidate.get("min"),
                        "max_value": candidate.get("max"),
                        "allowed_subset": allowed_subset,
                        "enum_map": _candidate_enum_map(slot_assets, candidate),
                        "source": "profiles_catalog",
                    })

    stats = {
        "profile_slots": len(slots),
        "node_defs": node_def_count,
        "controls": control_count,
        "records": len(records),
    }
    return records, stats


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


def _build_uom25_enum_map(slot_assets, control):
    if not slot_assets:
        return {}

    candidates = slot_assets.get("control_candidates", {}).get(control, [])
    nls_map = slot_assets.get("nls", {})
    if not candidates or not nls_map:
        return {}

    enum_map = {}
    for candidate in candidates:
        if candidate.get("uom") != 25:
            continue
        nls_prefix = candidate.get("nls")
        subset = candidate.get("subset")
        if not nls_prefix or subset is None:
            continue
        for raw_value in subset:
            key = str(raw_value)
            text = nls_map.get(f"{nls_prefix}-{key}")
            if text:
                enum_map[key] = text
    return enum_map


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
            value_str = "" if value_raw is None else str(value_raw)

            selected_candidate = _select_candidate(slot_assets, control, uom, value_str)
            selected_nls_prefix = selected_candidate.get("nls") if selected_candidate else None
            selected_editor_id = selected_candidate.get("editor_id") if selected_candidate else None
            selected_min = selected_candidate.get("min") if selected_candidate else None
            selected_max = selected_candidate.get("max") if selected_candidate else None
            selected_subset = None
            if selected_candidate and selected_candidate.get("subset") is not None:
                selected_subset = sorted(selected_candidate.get("subset"))

            enum_text = None
            enum_map = None
            if uom == 25 and value_raw is not None:
                built_enum_map = _build_uom25_enum_map(slot_assets, control)
                enum_map = built_enum_map or None
                enum_text = _resolve_uom25_enum_text(
                    slot_assets=slot_assets,
                    control=control,
                    value_str=str(value_raw),
                    fallback_text=formatted,
                )
                if enum_text is None and enum_map is not None:
                    enum_text = enum_map.get(str(value_raw))

            records.append({
                "node_id": str(node_id),
                "control": str(control),
                "uom": uom,
                "uom_label": UOM_LABELS.get(uom) if uom is not None else None,
                "source": "status_profile" if slot else "status_internal",
                "editor_id": selected_editor_id,
                "nls_prefix": selected_nls_prefix,
                "min_value": selected_min,
                "max_value": selected_max,
                "allowed_subset": selected_subset,
                "enum_map": enum_map,
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
    """Print active node/control mappings using the /rest/status profile-aware pipeline."""
    print("Requesting active node list from /rest/status...")
    records, stats = build_control_metadata_records(BASE_URL, USERNAME, PASSWORD)

    if not records:
        print("Failed to retrieve status/profile metadata records.")
        print(f"Stats: {stats}")
        return

    print(
        "Found "
        f"{stats.get('status_nodes', 0)} nodes, "
        f"{stats.get('status_properties', 0)} properties, "
        f"{stats.get('slots_loaded', 0)} slots."
    )
    print(f"Materialized {stats.get('records', len(records))} metadata records.\n")

    by_node = {}
    for rec in records:
        by_node.setdefault(rec["node_id"], []).append(rec)

    for node_id in sorted(by_node.keys()):
        print("==================================================")
        print(f"NODE: {node_id}")
        print("--------------------------------------------------")
        for rec in sorted(by_node[node_id], key=lambda row: row["control"]):
            control = rec["control"]
            uom = rec.get("uom")
            uom_label = rec.get("uom_label") or "Unknown"
            source = rec.get("source") or "unknown"
            enum_value = rec.get("enum_value")
            enum_text = rec.get("enum_text")

            msg = f"  -> {control}: uom={uom} ({uom_label}) source={source}"
            if enum_value is not None and enum_text:
                msg += f" enum[{enum_value}]={enum_text}"
            print(msg)


if __name__ == "__main__":
    parse_all_registered_nodes()