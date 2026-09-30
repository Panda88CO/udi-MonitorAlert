from __future__ import annotations

import re
from typing import Any


DEFAULT_DISCOVERY_CATEGORIES: list[dict[str, Any]] = [
    {
        "category": "irrigation",
        "description": "Irrigation, Sprinklers & Watering Systems",
        "uoms": (34, 35),  # Gallons, Liters
        "controls": ("FLOW", "WATER_FLOW", "RUNTIME", "IRRIGATION"),
        "keywords": (
            "irrigation", "sprinkler", "lawn", "soaker", "drip",
            "rachio", "orbit", "rainbird", "hydrawise", "garden",
            "backflow", "watering"
        ),
        "preset": "spike, creep, stuck",
    },
    {
        "category": "temperature",
        "description": "Temperature & Climate",
        "uoms": (4, 17),  # Celsius, Fahrenheit
        "controls": ("CLITEMP", "TEMP", "TEMPERATURE"),
        "keywords": (
            "temp", "temperature", "heat", "cool", "freeze",
            "freezer", "fridge", "refrigerator", "thermostat",
            "pool", "spa", "water temp", "boiler", "furnace", "attic"
        ),
        "preset": "spike, stuck, hourly",
    },
    {
        "category": "power",
        "description": "Power, Energy & Electrical Telemetry",
        "uoms": (73, 30, 74, 72, 119, 33),  # Watt, kW, Amp, Volt, WattHour, kWh
        "controls": ("WATTS", "POWER", "CURRENT_POWER", "ENERGY", "KWH", "TOTAL_POWER", "CPW"),
        "keywords": (
            "watt", "power", "energy", "kwh", "draw", "current",
            "amp", "solar", "grid", "inverter", "generation", "consumption"
        ),
        "preset": "hourly, spike",
    },
    {
        "category": "water",
        "description": "Water Flow, Volume & Leaks",
        "uoms": (34, 35),  # Gallon, Liter
        "controls": ("FLOW", "WATER_FLOW", "WATER"),
        "keywords": (
            "water", "flow", "leak", "gpm", "meter", "pump",
            "sump", "well", "gallon", "liter"
        ),
        "preset": "spike, creep, stuck",
    },
    {
        "category": "tank_level",
        "description": "Tanks, Fuel & Liquid Levels",
        "uoms": (6, 7, 8, 34, 35),  # Cu Ft, Cu M, Gallon, Liter
        "controls": ("TANK", "LEVEL", "DEPTH", "VOLUME"),
        "keywords": (
            "tank", "cistern", "propane", "fuel", "oil level",
            "sump pit", "well depth"
        ),
        "preset": "spike, stuck",
    },
    {
        "category": "humidity",
        "description": "Relative Humidity & Moisture",
        "uoms": (22,),  # RH%
        "controls": ("CLIHUM", "HUMIDITY", "MOISTURE"),
        "keywords": ("humidity", "moisture", "soil", "hygrometer"),
        "preset": "spike, stuck",
    },
    {
        "category": "pressure",
        "description": "Pressure & Vacuum",
        "uoms": (78, 79, 80, 82, 83),  # PSI, Bar, kPa, inHg, mbar
        "controls": ("BARPRES", "PRESSURE", "PSI"),
        "keywords": ("pressure", "psi", "barometer", "barometric"),
        "preset": "spike, stuck",
    },
    {
        "category": "battery",
        "description": "Battery Level & Health",
        "uoms": (),  # Percent handled conditionally in classify_candidate
        "controls": ("BATLVL", "BATTERY"),
        "keywords": ("battery", "batt"),
        "preset": "stuck(24h)",
    },
    {
        "category": "air_quality",
        "description": "Air Quality, Gases & Particulates",
        "uoms": (101, 102),  # PPM, PPB
        "controls": ("CO2", "AQI", "VOC", "PM25", "PM10"),
        "keywords": ("co2", "aqi", "voc", "pm2.5", "pm10", "radon", "air quality"),
        "preset": "spike, stuck",
    },
    {
        "category": "weather",
        "description": "Weather & Environmental Conditions",
        "uoms": (36, 48, 105, 106, 107, 116, 117, 118),  # Lux, Wind speed, Rain, Rain rate, Wind speed, Lux, UV, Solar rad
        "controls": ("LUMIN", "RAIN_RATE", "WIND_SPEED", "UV", "SOLRAD", "WINDIR"),
        "keywords": ("rain", "wind", "lux", "luminance", "weather", "uv index", "solar radiation"),
        "preset": "spike, stuck",
    },
]

# Mutable registry initialized with built-in rules
_CATEGORY_REGISTRY: list[dict[str, Any]] = list(DEFAULT_DISCOVERY_CATEGORIES)


def get_all_categories() -> list[dict[str, Any]]:
    """Return a copy of all registered discovery categories."""
    return list(_CATEGORY_REGISTRY)


def register_category(
    category: str,
    preset: str,
    uoms: tuple[int, ...] | list[int] = (),
    controls: tuple[str, ...] | list[str] = (),
    keywords: tuple[str, ...] | list[str] = (),
    description: str = "",
    priority_front: bool = True,
) -> None:
    """Register or replace a telemetry discovery category.

    If priority_front is True (default), it is evaluated before standard fallbacks.
    """
    rule = {
        "category": str(category).strip().lower(),
        "description": description or f"Custom category {category}",
        "uoms": tuple(int(u) for u in uoms),
        "controls": tuple(str(c).upper() for c in controls),
        "keywords": tuple(str(k).lower() for k in keywords),
        "preset": str(preset).strip(),
    }
    # Remove existing with same name if present
    for idx, existing in enumerate(_CATEGORY_REGISTRY):
        if existing.get("category") == rule["category"]:
            _CATEGORY_REGISTRY[idx] = rule
            return

    if priority_front:
        _CATEGORY_REGISTRY.insert(0, rule)
    else:
        _CATEGORY_REGISTRY.append(rule)


def reset_categories_to_default() -> None:
    """Reset the registry to the default built-in categories."""
    global _CATEGORY_REGISTRY
    _CATEGORY_REGISTRY = list(DEFAULT_DISCOVERY_CATEGORIES)


def _is_binary_switch(
    control: str,
    uom: int | None,
    min_value: float | None,
    max_value: float | None,
) -> bool:
    """Check if control is a standard binary on/off switch without continuous telemetry."""
    if uom in (73, 30, 74, 72, 119, 33, 4, 17, 34, 35, 78, 79, 80, 82, 83):
        return False
    ctrl_upper = str(control or "").upper()
    if ctrl_upper in ("ST", "STATUS"):
        if min_value is not None and max_value is not None:
            # Common binary ranges: 0-1, 0-100, 0-255
            if (min_value == 0.0 and max_value in (1.0, 100.0, 255.0)) and uom not in (4, 17, 73, 30, 33):
                return True
    return False


def classify_candidate(
    node_id: str,
    control: str,
    name: str | None = None,
    node_name: str | None = None,
    parent_node_name: str | None = None,
    uom: int | None = None,
    uom_label: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    is_timestamp_like: bool = False,
    categories: list[dict[str, Any]] | None = None,
) -> tuple[str, str] | None:
    """Classify a node/control telemetry candidate.

    Returns:
        (category_name, default_preset) or None if the control is excluded.
    """
    node_str = str(node_id or "").strip()
    ctrl_str = str(control or "").strip().upper()
    name_str = str(name or "").strip()
    node_name_str = str(node_name or "").strip()
    parent_node_name_str = str(parent_node_name or "").strip()
    uom_int = int(uom) if uom is not None else None

    # Filter 1: Timestamps
    if is_timestamp_like or uom_int in (151, 152):
        return None

    # Filter 2: Self-controller or node server root controllers
    node_lower = node_str.lower()
    if node_lower in ("ml_ctrl", "controller") or node_lower.endswith("_ctrl"):
        return None

    # Filter 3: Binary on/off switches without analog power telemetry
    if _is_binary_switch(ctrl_str, uom_int, min_value, max_value):
        return None

    active_categories = categories if categories is not None else _CATEGORY_REGISTRY

    # Check each registered category in priority order
    name_lower = name_str.lower()
    ctrl_lower = ctrl_str.lower()
    node_name_lower = node_name_str.lower()
    parent_node_name_lower = parent_node_name_str.lower()
    text_to_search = f"{parent_node_name_lower} {node_name_lower} {name_lower} {ctrl_lower}".strip()

    for cat_def in active_categories:
        cat_name = cat_def.get("category", "custom")
        preset = cat_def.get("preset", "spike, stuck")

        # 1. Match by UOM
        cat_uoms = cat_def.get("uoms", ())
        if uom_int is not None and uom_int in cat_uoms:
            # Special case for generic UOM 51 (Percent): require control or keyword match
            if uom_int == 51 and cat_name not in ("humidity", "battery"):
                pass
            elif uom_int == 51 and cat_name == "humidity":
                if any(kw in text_to_search for kw in ("humid", "moist", "soil")):
                    return cat_name, preset
            elif uom_int == 51 and cat_name == "battery":
                if any(kw in text_to_search for kw in ("batt", "battery")) or ctrl_str == "BATLVL":
                    return cat_name, preset
            else:
                return cat_name, preset

        # 2. Match by exact Control ID
        cat_controls = cat_def.get("controls", ())
        if ctrl_str in cat_controls:
            return cat_name, preset

        # 3. Match by Keywords in Name or Control
        cat_keywords = cat_def.get("keywords", ())
        for kw in cat_keywords:
            if kw and kw in text_to_search:
                return cat_name, preset

    return None

