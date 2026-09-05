import os
import re


# Shared suffixes used to match VDF/localization keys to voiceline filename stems.
KNOWN_SUFFIXES = [
    "_announcer",
    "_hero_3d",
    "_ability_3d",
    "_ult_3d",
    "_hero_announcer",
    "_hero_zipline_3d",
    "_ping_2d",
    "_idol",
    "_shopkeeper",
    "_hero",
    "_announcer_tutorial"
]

# Use longest-first ordering so more specific tags win when suffixes overlap.
ORDERED_KNOWN_SUFFIXES = tuple(sorted(KNOWN_SUFFIXES, key=len, reverse=True))

# Shared quoted key/value matcher for Valve-like KV lines: "key" "value"
# Supports escaped quotes inside either field.
QUOTED_KV_RE = re.compile(r'^"((?:\\.|[^"\\])*)"\s+"((?:\\.|[^"\\])*)"\s*$')


def parse_quoted_kv_line(line):
    """Parse a quoted KV line and return (key, value), or None if no match."""
    if line is None:
        return None
    match = QUOTED_KV_RE.match(line.strip())
    if not match:
        return None
    key, value = match.groups()
    key = key.replace('\\"', '"')
    value = value.replace('\\"', '"')
    return key, value


def load_vdf_key_text_map(vdf_path):
    """
    Load a VDF-like file and return {lower_key: text} from quoted KV lines.
    Non-matching lines are ignored.
    """
    data = {}
    if not vdf_path or not os.path.exists(vdf_path):
        return data

    with open(vdf_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            parsed = parse_quoted_kv_line(raw_line)
            if not parsed:
                continue
            key, text = parsed
            key = key.strip().lower()
            text = text.strip()
            if key:
                data[key] = text
    return data


def find_vdf_key_for_filename(filename, vdf_data):
    """
    Return the best matching VDF key for a filename:
    1) exact stem match
    2) stem + known suffix
    """
    if not vdf_data:
        return None

    stem = os.path.splitext(filename)[0].lower()
    if stem in vdf_data:
        return stem

    for suffix in ORDERED_KNOWN_SUFFIXES:
        candidate = f"{stem}{suffix}"
        if candidate in vdf_data:
            return candidate

    return None


def find_vdf_match_for_filename(filename, vdf_data):
    """Return (matching_key, text) or (None, None)."""
    key = find_vdf_key_for_filename(filename, vdf_data)
    if not key:
        return None, None
    return key, vdf_data.get(key)
