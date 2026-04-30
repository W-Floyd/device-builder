#!/usr/bin/env python3
"""
Generate definitions/components.json from ESPHome's pre-built schema bundle.

The schema repo (https://github.com/esphome/esphome-schema) publishes a
schema.zip per ESPHome release containing one JSON file per component.
That bundle drives VS Code's ESPHome extension and the official Builder
editor — it's the authoritative description of what each component
accepts in YAML. We use it as the primary source of structure, types,
defaults, and field descriptions.

A small amount of ESPHome introspection still happens for things the
schema doesn't capture:

- ``platform_defaults`` (``cv.SplitDefault`` per-target-platform values
  used by the backend to filter inapplicable fields)
- ``multi_conf`` (whether a component can be added more than once)
- ``supported_platforms`` (which target chips the component runs on)

Image URLs come from the docs repo's index page (the only MDX scraping
that survives the rewrite).

Usage
-----

    python script/sync_components.py                # latest stable release
    python script/sync_components.py --version 2026.4.3
    python script/sync_components.py --include-prereleases
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import urllib.request
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOGGER = logging.getLogger("sync_components")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_FILE = _REPO_ROOT / "esphome_device_builder" / "definitions" / "components.json"
_CACHE_ROOT = _REPO_ROOT / ".cache"

_RELEASES_API = "https://api.github.com/repos/esphome/esphome-schema/releases"
_SCHEMA_URL_TEMPLATE = "https://schema.esphome.io/{version}/schema.zip"
_DOCS_INDEX_URL = (
    "https://raw.githubusercontent.com/esphome/esphome-docs/current/"
    "src/content/docs/components/index.mdx"
)
_DOCS_REPO_URL = "https://github.com/esphome/esphome-docs.git"
_DOCS_REPO_BRANCH = "current"
_DOCS_CLONE_DIR = "esphome-docs"
_IMAGE_BASE_URL = "https://esphome.io/images/"

# CDN at schema.esphome.io rejects requests without a recognisable
# User-Agent. Use the project name + repo URL so any traffic is easy
# for the ESPHome team to identify.
_USER_AGENT = "esphome-device-builder-backend (https://github.com/esphome/device-builder-dashboard)"

# Top-level platform domains in the schema (also keys in our category enum).
# Components keyed as ``<id>.<domain>`` in the schema files — e.g.
# ``dht.sensor`` lives in dht.json under the key ``dht.sensor``.
_PLATFORM_DOMAINS: frozenset[str] = frozenset(
    {
        "sensor",
        "binary_sensor",
        "switch",
        "light",
        "fan",
        "cover",
        "climate",
        "button",
        "number",
        "select",
        "text",
        "text_sensor",
        "lock",
        "valve",
        "media_player",
        "speaker",
        "microphone",
        "camera",
        "display",
        "touchscreen",
        "output",
        "datetime",
        "event",
        "update",
        "alarm_control_panel",
        "stepper",
        "audio_adc",
        "audio_dac",
        "media_source",
        "one_wire",
        "canbus",
        "infrared",
        "time",
        "water_heater",
        "ota",
        "packet_transport",
    }
)

# Plain top-level keys we don't want to surface as user-facing components.
# ``core`` is the indexing-only metadata block in esphome.json.
_HIDDEN_TOP_LEVEL: frozenset[str] = frozenset({"core"})

# Map prebuilt-schema ``type`` strings to our ConfigEntryType enum.
# Things not in this table fall through to STRING.
_TYPE_MAP: dict[str, str] = {
    "boolean": "boolean",
    "integer": "integer",
    "string": "string",
    "enum": "string",  # SELECT-style; the underlying value is a string
    "pin": "pin",
    "schema": "nested",
    "trigger": "nested",
    "use_id": "id",
}

# ``data_type`` strings narrow the integer range or pick a different
# concrete type. Subset that maps cleanly onto our enum.
_DATA_TYPE_PRIMITIVE: dict[str, str] = {
    "positive_int": "integer",
    "positive_not_null_int": "integer",
    "uint8_t": "integer",
    "uint16_t": "integer",
    "uint32_t": "integer",
    "hex_uint8_t": "integer",
    "positive_float": "float",
    "port": "integer",
}

# Numeric bounds inferred from ``data_type``.
_DATA_TYPE_RANGE: dict[str, tuple[int, int]] = {
    "uint8_t": (0, 255),
    "hex_uint8_t": (0, 255),
    "uint16_t": (0, 65535),
    "uint32_t": (0, 4294967295),
    "port": (0, 65535),
}

# ``use_id_type`` is shaped ``"<namespace>::<ClassName>"``. Map the
# namespace to the catalog's component domain. ``switch_`` has a
# trailing underscore (the C++ namespace can't be ``switch``); we strip
# it. Everything else is identity.
_USE_ID_NAMESPACE_OVERRIDES: dict[str, str] = {
    "switch_": "switch",
    "binary_sensor": "binary_sensor",
    "text_sensor": "text_sensor",
}

# Fields whose key appears in this set get auto-detected as a secret
# value (renders masked in the form). Same heuristic as the previous
# sync — schema doesn't tag these explicitly.
_SECRET_KEY_FRAGMENTS = ("password", "passcode", "secret", "token", "api_key", "apikey")

# Schema-time keys we don't expose to the user (build-system / preload).
_SKIP_KEYS: frozenset[str] = frozenset({"mqtt_id", "zigbee_id", "then"})

# Per-component fields we don't surface in the catalog because they're
# deprecated and the dashboard handles the underlying concern itself.
# Keyed by ``(component_id, field_key)``.
#
# - ``esp32.board`` / ``esp8266.board``: the dashboard drives the
#   PlatformIO board ID from the user's board pick (the board catalog
#   is the source of truth). Internally we feed esphome with the
#   ``variant`` only, never ``board``.
_DEPRECATED_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("esp32", "board"),
        ("esp8266", "board"),
        ("rp2040", "board"),
        ("bk72xx", "board"),
        ("rtl87xx", "board"),
        ("ln882x", "board"),
    }
)

# Key-name prefixes for automation triggers (``on_press``, ``on_value``,
# ``on_state_change``, ...). These are config-variables in YAML but the
# frontend's form editor isn't where users wire automations — the
# automation editor is. Skip them.
_AUTOMATION_KEY_PREFIXES: tuple[str, ...] = ("on_",)

# Map from the ``**type**`` doc prefix marker to our ConfigEntryType.
# The schema docs lead with bracketed type names (``**[Time](...)**:``)
# or bold scalars (``**boolean**:``); we strip the markup and look up
# the resulting key here.
_DOC_PREFIX_TYPES: dict[str, str] = {
    "Time": "time_period",
    "Time Period": "time_period",
    "MAC Address": "mac_address",
    "MAC": "mac_address",
    "Pin": "pin",
    "Color": "color",
    "Lambda": "lambda",
    "Icon": "icon",
    "boolean": "boolean",
    "float": "float",
    "string": "string",
    "int": "integer",
    "Action": "nested",
    "Automation": "nested",
}

# Time-period default values are short strings like ``"60s"``,
# ``"5min"``, ``"1h30s"``. This regex matches that shape.
_TIME_PERIOD_DEFAULT = re.compile(r"^\d+(\.\d+)?\s*(ms|us|ns|s|min|h|d)(\d+\s*\w+)*$")

# Base entity / framework fields that always render under "Advanced" by
# default — valid but rarely tweaked. Same set as the previous sync.
_ADVANCED_BASE_KEYS: frozenset[str] = frozenset(
    {
        "internal",
        "disabled_by_default",
        "entity_category",
        "state_class",
        "accuracy_decimals",
        "force_update",
        "setup_priority",
        "expire_after",
        "filters",
        "interlock",
        "interlock_wait_time",
        # MQTT entity options
        "qos",
        "retain",
        "discovery",
        "subscribe_qos",
        "state_topic",
        "command_topic",
        "availability",
        # Zigbee entity options
        "zigbee_sensor",
        "zigbee_switch",
        "zigbee_binary_sensor",
        "zigbee_button",
        "zigbee_cover",
        "zigbee_climate",
        "zigbee_fan",
        "zigbee_light",
        "zigbee_lock",
        "zigbee_number",
        "zigbee_select",
        "zigbee_text",
        "zigbee_text_sensor",
    }
)

# Order in which entries appear in the rendered form. The advanced/
# main-form split is decided separately — this just controls relative
# rank within each section.
_IMPORTANT_KEY_ORDER: tuple[str, ...] = (
    # Discriminators first — they decide which other fields render.
    "platform",
    "type",
    "framework",  # esp32 / esp8266 framework selector (arduino vs esp-idf)
    # Identification
    "name",
    "friendly_name",
    "icon",
    # Credentials / connection
    "ssid",
    "password",
    "broker",
    "username",
    # Hardware
    "pin",
    "address",
    "i2c_id",
    "spi_id",
    "uart_id",
    # Behaviour
    "device_class",
    "unit_of_measurement",
    "restore_mode",
    "update_interval",
    "model",
    "variant",
    "inverted",
    # Common esphome-block metadata
    "area",
    # Important fields that stay flagged advanced — keep their sort
    # priority but render under the "Advanced" section.
    "id",
    "comment",
)
_IMPORTANT_KEYS: frozenset[str] = frozenset(_IMPORTANT_KEY_ORDER)
# Subset of important keys that stay flagged advanced (id keeps its
# sort priority but always lives under the advanced section).
_ADVANCED_IMPORTANT_KEYS: frozenset[str] = frozenset({"id", "comment"})

# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point — parse args, fetch schema, generate JSON."""
    logging.basicConfig(format="%(message)s", level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Generate components.json from ESPHome's pre-built schema bundle.",
    )
    parser.add_argument(
        "--version",
        help="ESPHome release tag to use (e.g. '2026.4.3'). Defaults to the latest GitHub release.",
    )
    parser.add_argument(
        "--include-prereleases",
        action="store_true",
        help="When auto-selecting the latest release, also consider prereleases.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Wipe cached schemas before fetching.",
    )
    parser.add_argument(
        "--limit-component",
        action="append",
        default=[],
        help="If given, only emit catalog entries for the listed component "
        "ids (e.g. ``--limit-component dht --limit-component wifi``). "
        "For local debugging.",
    )
    args = parser.parse_args()

    if args.clean and _CACHE_ROOT.exists():
        for d in _CACHE_ROOT.glob("esphome-schema-*"):
            shutil.rmtree(d)

    version = args.version or resolve_latest_release(
        include_prereleases=args.include_prereleases,
    )
    _LOGGER.info("Using ESPHome schema version: %s", version)

    schema_dir = ensure_schema(version)
    _LOGGER.info("Schema cached at: %s", schema_dir)

    catalog = build_catalog(
        schema_dir=schema_dir,
        limit=set(args.limit_component) or None,
    )
    _LOGGER.info(
        "Built catalog: %d components, %d with config entries",
        len(catalog),
        sum(1 for c in catalog if c.get("config_entries")),
    )

    payload = {
        "esphome_schema_version": version,
        "components": [_strip_defaults(c) for c in catalog],
    }
    _OUTPUT_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _LOGGER.info("Wrote %s", _OUTPUT_FILE)
    return 0


# ---------------------------------------------------------------------------
# Schema fetcher (versioned, cached)
# ---------------------------------------------------------------------------


def resolve_latest_release(*, include_prereleases: bool = False) -> str:
    """Return the latest release tag from the esphome-schema repo."""
    _LOGGER.info("Fetching latest release tag from GitHub...")
    releases = json.loads(_http_get(_RELEASES_API))
    for r in releases:
        if r.get("draft"):
            continue
        if r.get("prerelease") and not include_prereleases:
            continue
        return r["tag_name"]
    msg = "No suitable release found on esphome-schema repo"
    raise RuntimeError(msg)


def ensure_schema(version: str) -> Path:
    """Download and unpack the schema bundle for *version* if not cached."""
    cache_dir = _CACHE_ROOT / f"esphome-schema-{version}"
    schema_dir = cache_dir / "schema"
    if schema_dir.exists() and any(schema_dir.iterdir()):
        return schema_dir

    cache_dir.mkdir(parents=True, exist_ok=True)
    url = _SCHEMA_URL_TEMPLATE.format(version=version)
    _LOGGER.info("Downloading %s", url)
    data = _http_get(url, timeout=120)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        zf.extractall(cache_dir)

    if not schema_dir.exists():
        msg = f"Schema bundle layout unexpected — missing {schema_dir}"
        raise RuntimeError(msg)
    return schema_dir


def _http_get(url: str, *, timeout: int = 30) -> bytes:
    """GET *url* with our identifying User-Agent and return raw bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


@dataclass
class SchemaIndex:
    """Pre-built docs index for every component in the schema bundle.

    Three indexes feed this:

    - ``esphome.json -> core.components[<id>]`` — non-platform components
      (wifi, api, esp32_ble_tracker, ...) carrying ``docs`` and
      ``dependencies`` lists.
    - ``esphome.json -> core.platforms[<domain>]`` — the platform domain
      entries themselves (sensor, switch, ...).
    - ``<domain>.json -> <domain>.components[<id>]`` — every platform-
      providing component (dht under sensor, gpio under switch, ...).
      These carry only ``docs`` (no dependencies — derive from the
      domain).

    All three are merged under one key shape so callers don't need to
    know which index a component lives in.
    """

    # Maps the catalog id (``<domain>.<stem>`` for platform-providing
    # components, bare id otherwise) to its metadata block.
    metadata: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_index(schema_dir: Path) -> SchemaIndex:
    """Read every index in the bundle into one merged SchemaIndex."""
    metadata: dict[str, dict[str, Any]] = {}

    # 1. esphome.json — core.components and core.platforms.
    try:
        core = (json.loads((schema_dir / "esphome.json").read_text()) or {}).get("core") or {}
    except FileNotFoundError:
        core = {}
    for cid, meta in (core.get("components") or {}).items():
        metadata[cid] = meta or {}
    for pid, meta in (core.get("platforms") or {}).items():
        metadata[pid] = meta or {}

    # 2. Each <domain>.json — domain.components map. Key under both the
    # bare stem and the qualified ``<domain>.<stem>`` form so lookups
    # work regardless of which the caller has on hand.
    for domain in _PLATFORM_DOMAINS:
        domain_file = schema_dir / f"{domain}.json"
        if not domain_file.exists():
            continue
        try:
            domain_raw = json.loads(domain_file.read_text())
        except Exception:  # noqa: S112 — index-only file, broken JSON is non-fatal
            continue
        section = domain_raw.get(domain) or {}
        for cid, meta in (section.get("components") or {}).items():
            qualified = f"{domain}.{cid}"
            metadata.setdefault(qualified, meta or {})
            metadata.setdefault(cid, meta or {})

    return SchemaIndex(metadata=metadata)


def iter_schema_files(schema_dir: Path) -> Iterable[Path]:
    """Yield every <component>.json under *schema_dir*."""
    yield from sorted(schema_dir.glob("*.json"))


# ---------------------------------------------------------------------------
# Description cleaner
# ---------------------------------------------------------------------------


# Leading ``**type**:`` prefix pasted in front of every field doc.
_DOCS_TYPE_PREFIX = re.compile(
    r"^\*\*[^*]+\*\*\s*[:\-]\s*",
)

# Trailing ``*See also: [Name](url)*`` link — we extract the URL as
# help_link / docs_url and then drop the footer from the description.
_DOCS_SEE_ALSO = re.compile(
    r"\s*\*See also:\s*\[([^\]]+)\]\(([^)]+)\)\*\s*$",
)


@dataclass
class CleanedDocs:
    text: str
    name: str | None = None  # extracted from "[Name](url)" link
    url: str | None = None


def clean_docs(raw: str | None) -> CleanedDocs:
    """Strip type prefix and ``See also`` footer; surface both as fields."""
    if not raw:
        return CleanedDocs("")
    text = raw.strip()
    name: str | None = None
    url: str | None = None
    m = _DOCS_SEE_ALSO.search(text)
    if m:
        name = m.group(1).strip()
        url = m.group(2).strip()
        text = text[: m.start()].rstrip()
    text = _DOCS_TYPE_PREFIX.sub("", text)
    return CleanedDocs(text=text.strip(), name=name, url=url)


# ---------------------------------------------------------------------------
# Build catalog (top-level)
# ---------------------------------------------------------------------------


def build_catalog(
    *,
    schema_dir: Path,
    limit: set[str] | None = None,
) -> list[dict]:
    """Walk every schema file and produce ConfigCatalogEntry-shaped dicts."""
    index = load_index(schema_dir)
    image_map = load_image_map()
    out: list[dict] = []
    for path in iter_schema_files(schema_dir):
        try:
            entries = build_entries_from_file(path, index, schema_dir, image_map)
        except Exception:
            _LOGGER.exception("Failed to build catalog entries from %s", path.name)
            continue
        for entry in entries:
            if limit and entry["id"] not in limit:
                continue
            out.append(entry)

    # Layer MDX-frontmatter descriptions onto components whose
    # schema-supplied description is empty. This patches the upstream
    # gap where the prebuilt schema's component index lists per-platform
    # components with only ``dependencies`` (e.g. ``ota.esphome``,
    # ``ota.http_request``).
    _backfill_descriptions_from_mdx(out)

    return out


def _backfill_descriptions_from_mdx(entries: list[dict]) -> None:
    """Fill empty descriptions from the docs MDX frontmatter, in place.

    The prebuilt schema's component index sometimes only lists
    ``dependencies`` for a platform-providing component, leaving the
    description blank. The MDX docs page for the same component
    *does* carry a ``description:`` frontmatter field (and a curated
    intro paragraph). Pull from there as a one-time enrichment.

    Silently skipped when the docs repo can't be cloned/fetched.
    """
    missing = [e for e in entries if not (e.get("description") or "").strip()]
    if not missing:
        return
    descriptions = _load_mdx_descriptions()
    if not descriptions:
        return
    backfilled = 0
    for entry in missing:
        cid = entry["id"]
        text = descriptions.get(cid)
        if not text:
            # Try the bare stem as a last resort (e.g. for hub-style
            # components whose docs file isn't under <domain>/).
            stem = cid.split(".", 1)[-1]
            text = descriptions.get(stem)
        if text:
            entry["description"] = text
            backfilled += 1
    if backfilled:
        _LOGGER.info("Backfilled %d descriptions from docs MDX", backfilled)


def _load_mdx_descriptions() -> dict[str, str]:
    """Walk the cached docs repo, return ``{component_id: description}``.

    Each per-component MDX page lives under
    ``src/content/docs/components/<domain>/<stem>.mdx`` (platform-
    providing components) or ``src/content/docs/components/<bare>.mdx``
    (everything else). The frontmatter ``description:`` field is the
    primary source — short, curated, written for catalog/preview use.
    Falls back to the first prose paragraph when the frontmatter
    description is missing.

    Caches the cloned docs repo in ``.cache/esphome-docs/`` so re-runs
    don't refetch.
    """
    docs_dir = _ensure_docs_repo()
    if docs_dir is None:
        return {}

    out: dict[str, str] = {}
    components_root = docs_dir / "src" / "content" / "docs" / "components"
    if not components_root.exists():
        return {}

    for mdx_path in components_root.rglob("*.mdx"):
        rel = mdx_path.relative_to(components_root)
        parts = rel.with_suffix("").parts
        if not parts or parts[-1] == "index":
            continue
        if len(parts) == 1:
            component_id = parts[0]
        elif len(parts) == 2:
            component_id = f"{parts[0]}.{parts[1]}"
        else:
            continue  # Deeper nesting isn't a per-component page.

        text = _extract_mdx_description(mdx_path.read_text(encoding="utf-8"))
        if text:
            out[component_id] = text
            # Also index under the bare stem if it's not already taken,
            # so e.g. ``ota.esphome`` falls back to ``esphome.mdx`` if
            # ever needed (rare, but cheap to support).
            stem = parts[-1]
            out.setdefault(stem, text)
    return out


def _ensure_docs_repo() -> Path | None:
    """Clone or update the esphome-docs repo (shallow). Returns its path."""
    import subprocess

    target = _CACHE_ROOT / _DOCS_CLONE_DIR
    if (target / ".git").exists():
        # Refresh in-place. ``-q`` and ``--ff-only`` keep it quiet and
        # safe; failure here just means we keep using the existing
        # snapshot.
        subprocess.run(
            ["git", "-C", str(target), "pull", "-q", "--ff-only"],
            check=False,
            timeout=60,
        )
        return target
    if target.exists():
        # Pre-existing non-git directory — leave alone, use as-is.
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    _LOGGER.info("Cloning esphome-docs (shallow) to %s", target)
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--depth=1",
                "--single-branch",
                f"--branch={_DOCS_REPO_BRANCH}",
                _DOCS_REPO_URL,
                str(target),
            ],
            check=True,
            timeout=120,
        )
    except Exception:
        _LOGGER.warning("Could not clone esphome-docs — descriptions stay empty")
        return None
    return target


# Frontmatter description matcher — captures the value of the
# ``description:`` field at the start of the file. Handles both quoted
# and bare values.
_FRONTMATTER_DESCRIPTION = re.compile(
    r'^description:\s*"([^"]+)"|^description:\s*\'([^\']+)\'|^description:\s*([^\n]+)$',
    re.MULTILINE,
)


def _extract_mdx_description(text: str) -> str:
    """Return the curated description for a component MDX file.

    Tries the frontmatter ``description:`` field first; falls back to
    the first prose paragraph (after frontmatter, skipping JSX imports
    and HTML anchors) if frontmatter has no description.
    """
    front_end = text.find("---", 4) if text.startswith("---") else -1
    front = text[:front_end] if front_end > 0 else ""
    body = text[front_end + 3 :] if front_end > 0 else text

    m = _FRONTMATTER_DESCRIPTION.search(front)
    if m:
        value = next(g for g in m.groups() if g)
        cleaned = _clean_description_text(value.strip())
        if cleaned:
            return cleaned

    # Fall back to the first prose paragraph.
    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith(("import ", "<", ":::", "#", "```", "{")):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))

    for p in paragraphs:
        cleaned = _clean_description_text(p)
        if cleaned:
            return cleaned
    return ""


# Markdown link / inline-code stripping for description text.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_BOLD_ITALIC_RE = re.compile(r"\*{1,3}([^*]+)\*{1,3}")


def _clean_description_text(text: str) -> str:
    """Flatten markdown markup so descriptions read as plain prose."""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    text = _MD_BOLD_ITALIC_RE.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    # ESPHome docs use a few stock leading phrases that don't add info.
    for phrase in (
        "Instructions for setting up the ",
        "Instructions for setting up ",
        "Instructions for using the ",
        "Instructions for using ",
    ):
        if text.lower().startswith(phrase.lower()):
            text = text[len(phrase) :]
            text = text[:1].upper() + text[1:] if text else text
            break
    return text


def load_image_map() -> dict[str, str]:
    """Parse the docs ``components/index.mdx`` for image URLs.

    The index page renders a tiled list of components where each entry
    is a JSX-array literal:

        ["Name", "/components/<category>/<id>/", "<image>.svg", ...]

    We match those rows and produce a ``component_id -> image_url`` map
    where ``component_id`` matches our catalog ids (qualified with
    ``<domain>.<id>`` for platform-providing components).

    No ImagesMap if the docs file can't be fetched — image_url stays
    empty for every component.
    """
    cache_file = _CACHE_ROOT / "esphome-docs-index.mdx"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    if not cache_file.exists():
        try:
            cache_file.write_bytes(_http_get(_DOCS_INDEX_URL))
        except Exception:
            _LOGGER.warning(
                "Could not fetch docs index page — image URLs will be empty",
            )
            return {}

    text = cache_file.read_text(errors="ignore")
    pattern = re.compile(
        r'\["([^"]+)",\s*"(/components/[^"]+)",\s*"([^"]+)"',
    )
    out: dict[str, str] = {}
    for _name, path, image in pattern.findall(text):
        # Path examples:
        #   /components/wifi/         -> "wifi"
        #   /components/sensor/dht/   -> "sensor.dht"
        #   /components/sensor/       -> "sensor" (the domain page itself)
        parts = [p for p in path.strip("/").split("/")[1:] if p]
        if not parts:
            continue
        if len(parts) >= 2:
            component_id = f"{parts[0]}.{parts[1]}"
        else:
            component_id = parts[0]
        out.setdefault(component_id, _IMAGE_BASE_URL + image)
        # Also store under the bare stem when only one platform exists,
        # so lookups by either id work.
        if len(parts) >= 2:
            out.setdefault(parts[1], _IMAGE_BASE_URL + image)
    _LOGGER.info("Image map built: %d components", len(out))
    return out


def build_entries_from_file(
    path: Path,
    index: SchemaIndex,
    schema_dir: Path,
    image_map: dict[str, str],
) -> list[dict]:
    """Build one or more catalog entries from a single schema JSON file."""
    raw = json.loads(path.read_text())
    out: list[dict] = []
    for top_key, section in raw.items():
        if top_key in _HIDDEN_TOP_LEVEL:
            continue
        if not isinstance(section, dict):
            continue
        entry = build_component_entry(top_key, section, index, schema_dir, image_map)
        if entry is not None:
            out.append(entry)
    return out


def build_component_entry(
    top_key: str,
    section: dict,
    index: SchemaIndex,
    schema_dir: Path,
    image_map: dict[str, str],
) -> dict | None:
    """Convert one ``<id>.json`` top-level entry to our catalog shape.

    The schema's qualifier order is ``<stem>.<domain>`` (e.g.
    ``dht.sensor``). We surface ids as ``<domain>.<stem>`` to match the
    rest of our codebase.

    Returns None for entries that don't represent a user-facing
    component: bare platform-domain headers (``sensor``, ``switch``)
    and schema-only entries (``bme280_base``, ``as3935`` hub) without
    their own ``CONFIG_SCHEMA``.
    """
    if not _has_config_schema(section):
        return None

    domain, stem = _split_qualified_key(top_key)
    if domain in _PLATFORM_DOMAINS:
        category = domain
        component_id = f"{domain}.{stem}"
    elif top_key in _PLATFORM_DOMAINS:
        # The bare platform domain itself (sensor:, switch:, ...) — not
        # a user-facing component. Skip.
        return None
    else:
        category = _infer_misc_category(top_key)
        component_id = top_key

    config_entries = _extract_config_entries(
        section,
        schema_dir=schema_dir,
        component_id=component_id,
    )

    meta = _lookup_index_meta(component_id, top_key, index)
    docs = clean_docs(meta.get("docs"))
    dependencies = list(meta.get("dependencies") or [])

    # Narrow esphome introspection — adds multi_conf, platform_defaults,
    # supported_platforms, and refined types (boolean/float/...) the
    # schema bundle doesn't surface. No-ops when esphome isn't
    # importable.
    introspection = introspect_component(stem if domain else top_key)
    _apply_platform_defaults(config_entries, introspection.get("platform_defaults") or {})
    _apply_refined_types(config_entries, introspection.get("refined_types") or {})

    return {
        "id": component_id,
        "name": _resolve_name(component_id, stem, docs.name),
        "description": docs.text,
        "category": category,
        "docs_url": _strip_anchor(docs.url or ""),
        "image_url": image_map.get(component_id) or image_map.get(stem) or "",
        "dependencies": dependencies,
        "multi_conf": introspection.get("multi_conf", False),
        "supported_platforms": _derive_supported_platforms(
            stem if domain else top_key,
            dependencies,
            introspection,
        ),
        "config_entries": config_entries,
    }


# ---------------------------------------------------------------------------
# Schema → ConfigEntry conversion
# ---------------------------------------------------------------------------


def _extract_config_entries(
    section: dict,
    *,
    schema_dir: Path,
    component_id: str = "",
) -> list[dict]:
    """Walk ``schemas.CONFIG_SCHEMA`` and produce our ConfigEntry list.

    Resolves ``extends`` references inline so the entry list reflects
    the merged schema the user will see (e.g. ``dht.sensor.humidity``
    inherits the base ``sensor._SENSOR_SCHEMA`` fields). The
    ``component_id`` is used to filter ``_DEPRECATED_FIELDS`` at the
    top level only — nested fields with the same name are unaffected.
    """
    schemas = section.get("schemas") or {}
    config_schema = schemas.get("CONFIG_SCHEMA") or {}
    schema = config_schema.get("schema") or {}
    if not schema:
        return []
    return _convert_config_vars(schema, schema_dir, component_id=component_id)


def _convert_config_vars(
    schema_node: dict,
    schema_dir: Path,
    *,
    component_id: str = "",
) -> list[dict]:
    """Convert a ``schema`` node (config_vars + extends) to a list of entries."""
    config_vars = dict(schema_node.get("config_vars") or {})

    # Inline ``extends`` references — fields from referenced base
    # schemas appear before the local ones, then local ones override.
    extended: dict[str, dict] = {}
    for ref in schema_node.get("extends") or []:
        extended.update(_resolve_extends(ref, schema_dir))
    merged = {**extended, **config_vars}

    out: list[dict] = []
    for key, raw in merged.items():
        if key in _SKIP_KEYS:
            continue
        if (component_id, key) in _DEPRECATED_FIELDS:
            continue
        if any(key.startswith(p) for p in _AUTOMATION_KEY_PREFIXES):
            continue
        entry = _convert_field(key, raw or {}, schema_dir)
        if entry is None:
            continue
        out.append(entry)
    return _sort_entries(out)


def _resolve_extends(ref: str, schema_dir: Path) -> dict[str, dict]:
    """Look up an ``extends`` reference and return its config_vars.

    *ref* is shaped ``<file>.<schema_name>`` — e.g.
    ``sensor._SENSOR_SCHEMA``, ``core.positive_time_period_milliseconds``.
    For schemas that themselves carry ``extends`` we recurse so the full
    ancestry is flattened into one config_vars dict.
    """
    parts = ref.split(".")
    if len(parts) < 2:
        return {}
    file_name = parts[0]
    schema_name = ".".join(parts[1:])
    path = schema_dir / f"{file_name}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())

    # The schema may live under ``raw[file_name]`` (our usual case) or
    # at deeper qualified keys. Search both layers.
    candidates: list[dict] = []
    for top_value in raw.values():
        if not isinstance(top_value, dict):
            continue
        schemas = top_value.get("schemas") or {}
        if schema_name in schemas:
            candidates.append(schemas[schema_name])

    if not candidates:
        return {}
    target = candidates[0]
    schema_node = target.get("schema") or {}
    inner = dict(schema_node.get("config_vars") or {})
    # Recurse into nested extends so the merged map carries everything.
    for sub in schema_node.get("extends") or []:
        for k, v in _resolve_extends(sub, schema_dir).items():
            inner.setdefault(k, v)
    return inner


def _convert_field(key: str, raw: dict, schema_dir: Path) -> dict | None:
    """Build a single ConfigEntry dict from a schema's config_var entry."""
    if not isinstance(raw, dict):
        # Some schemas use bare ``{}``-shaped placeholders for fields
        # whose details live in an extends-referenced base. Treat as
        # plain string optional.
        raw = {}

    # Required vs Optional vs GeneratedID
    schema_key = raw.get("key")
    required = schema_key == "Required"

    schema_type = raw.get("type")
    inner_schema = raw.get("schema")
    data_type = raw.get("data_type")

    # Own-id fields ⇒ always rendered as a free-form id input. The
    # ``key: GeneratedID`` form auto-generates and stays under
    # "Advanced"; the ``key: Required`` + ``id_type`` form is a
    # required user-supplied id (e.g. ``output.gpio.id``).
    if _is_own_id_field(raw):
        return _build_id_entry(key, raw, required=required)

    # Resolve the entry type. Priority: explicit type → data_type
    # → enum shape → extends → docs hints → default-value hints → string.
    docs_text = raw.get("docs") or ""
    extends = (inner_schema or {}).get("extends") if isinstance(inner_schema, dict) else None

    entry_type = _TYPE_MAP.get(schema_type or "")
    if entry_type is None and data_type in _DATA_TYPE_PRIMITIVE:
        entry_type = _DATA_TYPE_PRIMITIVE[data_type]

    # An ``enum`` whose values are ``true`` and ``false`` is really a
    # boolean — the schema uses cv.boolean which produces this shape.
    if (entry_type == "string" or entry_type is None) and _looks_like_boolean_enum(raw):
        entry_type = "boolean"
    elif entry_type is None and "values" in raw:
        entry_type = "string"  # enum-shaped (options below)

    # ``extends: ["core.positive_time_period_*"]`` collapses to time_period
    # — even when the schema marked the entry as ``type: schema`` (most
    # _SENSOR_SCHEMA fields like ``expire_after`` come through that way).
    if extends and not (inner_schema or {}).get("config_vars"):
        for ref in extends:
            if "time_period" in ref:
                entry_type = "time_period"
                break
            if ref.endswith(".positive_float") or ref.endswith(".float_"):
                entry_type = "float"
                break
            if "positive_int" in ref or ref.endswith(".int_"):
                entry_type = "integer"
                break

    # Docs-prefix hints — fields without explicit type lead with
    # ``**type**:`` markers we can parse out.
    if entry_type is None:
        prefix = _docs_type_marker(docs_text)
        entry_type = _DOC_PREFIX_TYPES.get(prefix)

    # Default-value hints — bare time strings like ``"60s"``, ``"5min"``.
    if entry_type is None and _looks_like_time_period_default(raw.get("default")):
        entry_type = "time_period"

    # Key-name fallback — ``icon`` / ``mac_address`` are usually
    # untyped strings in the schema.
    if entry_type is None:
        if key == "icon":
            entry_type = "icon"

    if entry_type is None and inner_schema and inner_schema.get("config_vars"):
        entry_type = "nested"
    if entry_type is None:
        entry_type = "string"

    # Type promotion: schema-given ``string`` whose key/name implies a
    # secret -> secure_string.
    if entry_type == "string" and any(frag in key.lower() for frag in _SECRET_KEY_FRAGMENTS):
        entry_type = "secure_string"

    # Cleaned docs ⇒ description + help_link/docs_url candidate.
    docs = clean_docs(raw.get("docs"))
    references = _resolve_use_id_reference(raw)

    # Structural fields (wiring + pin selection) are kept on the main
    # form even when optional — users almost always want to see what's
    # wired to what.
    is_structural = entry_type == "pin" or bool(references)
    advanced = _classify_advanced(key, required=required, is_structural=is_structural)

    entry: dict[str, Any] = {
        "key": key,
        "type": entry_type,
        "label": _key_to_label(key),
        "description": docs.text or None,
        "required": required,
        "default_value": _coerce_default(raw.get("default")),
        "options": _build_options(raw),
        "allow_custom_value": False,
        "range": list(_DATA_TYPE_RANGE[data_type]) if data_type in _DATA_TYPE_RANGE else None,
        "multi_value": bool(raw.get("is_list")),
        "templatable": bool(raw.get("templatable")),
        "depends_on": None,
        "depends_on_value": None,
        "depends_on_value_not": None,
        "depends_on_component": None,
        "references_component": references,
        "pin_features": _resolve_pin_features(raw) if entry_type == "pin" else [],
        "pin_mode": None,
        "advanced": advanced,
        "hidden": False,
        "help_link": docs.url,
        "translation_key": None,
        "translation_params": None,
        "platform_type": None,
    }

    # Detect user-keyed maps (``key_type`` set in the raw entry).
    # ``logger.logs``, ``substitutions:`` and similar enumerate every
    # possible *valid* key as a separate config_var with the same
    # value-shape — that's a representation of "any string key,
    # uniform value type", not hundreds of distinct sub-fields.
    # Collapse to a single value template so the frontend can render a
    # dynamic ``add row`` editor instead of a wall of cloned forms.
    if "key_type" in raw and isinstance(inner_schema, dict):
        entry["type"] = "map"
        entry["config_entries"] = _build_map_value_template(inner_schema, schema_dir)
        return entry

    # Recurse into nested schemas for type=nested.
    if entry_type == "nested" and isinstance(inner_schema, dict):
        inner = _convert_config_vars(inner_schema, schema_dir)
        entry["config_entries"] = inner or None
        entry["platform_type"] = _detect_platform_type(inner_schema)
        # When every child would render as advanced anyway, hide the
        # parent's expand affordance under "Advanced" too — no point
        # surfacing an empty group on the main form. We don't pull the
        # parent BACK to non-advanced based on a visible child:
        # required sub-fields like ``framework.components.name`` are
        # only meaningful when the user has chosen to use that group,
        # so leaving the parent's classification to ``_classify_advanced``
        # avoids accidentally exposing deeply technical groups.
        if inner and _all_inner_advanced(inner):
            entry["advanced"] = True
    else:
        entry["config_entries"] = None

    return entry


def _build_map_value_template(
    inner_schema: dict,
    schema_dir: Path,
) -> list[dict] | None:
    """Build a single-entry list describing the value type of a map field.

    The schema's ``key_type`` pattern enumerates every accepted key as
    a config_var carrying the value's shape. Take the first one as a
    template (they're all identical for true maps; any inconsistency
    is upstream noise we can safely flatten). The entry is keyed
    ``"value"`` so the frontend has a stable binding name.
    """
    config_vars = inner_schema.get("config_vars") or {}
    if not config_vars:
        return None
    sample_raw = next(iter(config_vars.values()))
    if not isinstance(sample_raw, dict):
        return None
    template = _convert_field("value", sample_raw, schema_dir)
    return [template] if template else None


def _build_id_entry(key: str, raw: dict, *, required: bool = False) -> dict:
    """Build a ConfigEntry for an own-id field.

    Auto-generated ids (``key: GeneratedID``) stay flagged advanced —
    most users let ESPHome derive them. Required / Optional ids
    (``key: Required`` with ``id_type``) stay on the main form because
    the user has to supply them. ``references_component`` is never set:
    own ids are free-form names, not references to other components.
    """
    docs = clean_docs(raw.get("docs"))
    is_generated = raw.get("key") == "GeneratedID"
    return {
        "key": key,
        "type": "id",
        "label": _key_to_label(key),
        "description": docs.text or None,
        "required": required,
        "default_value": None,
        "options": None,
        "allow_custom_value": False,
        "range": None,
        "multi_value": False,
        "templatable": False,
        "depends_on": None,
        "depends_on_value": None,
        "depends_on_value_not": None,
        "depends_on_component": None,
        "references_component": None,
        "pin_features": [],
        "pin_mode": None,
        "advanced": is_generated and not required,
        "hidden": False,
        "help_link": docs.url,
        "translation_key": None,
        "translation_params": None,
        "config_entries": None,
        "platform_type": None,
    }


def _build_options(raw: dict) -> list[dict] | None:
    """Build a list of ``{label, value}`` dicts from a schema's enum values."""
    values = raw.get("values")
    if not isinstance(values, dict):
        return None
    options: list[dict] = []
    for value, info in values.items():
        label = value if value else "(none)"
        if isinstance(info, dict) and info.get("docs"):
            label = info["docs"]
        options.append({"label": label, "value": value})
    return options or None


def _coerce_default(value: Any) -> Any:
    """Pass through scalar defaults; coerce schema-string trues/falses."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return value


def _resolve_use_id_reference(raw: dict) -> str | None:
    """Map ``use_id_type: 'ns::Class'`` to a component domain.

    ``use_id_type`` is the schema's marker for *cross-references* —
    fields like ``i2c_id`` that point at another component instance.
    Do NOT confuse with ``id_type``, which describes the type of id
    this field *creates* (its own id) — those are free-form strings,
    not references.
    """
    use_id_type = raw.get("use_id_type")
    if not isinstance(use_id_type, str) or "::" not in use_id_type:
        return None
    namespace = use_id_type.split("::", 1)[0]
    return _USE_ID_NAMESPACE_OVERRIDES.get(namespace, namespace)


def _is_own_id_field(raw: dict) -> bool:
    """Return True iff this field defines the component's own id.

    Two shapes signal an own-id:
      - ``key: "GeneratedID"`` — auto-generated id (rare to set manually)
      - ``id_type: { class: ... }`` without ``use_id_type`` — required
        or optional id field whose type the schema knows but which is
        still the *component's own* identifier, not a reference.
    """
    if raw.get("key") == "GeneratedID":
        return True
    if isinstance(raw.get("id_type"), dict) and "use_id_type" not in raw:
        return True
    return False


def _resolve_pin_features(raw: dict) -> list[str]:
    """Translate the schema's ``modes`` list into our PinFeature enum keys."""
    modes = raw.get("modes") or []
    out: list[str] = []
    for m in modes:
        # Schema uses ``input``/``output``/``pullup``/``pulldown`` etc.
        # Our PinFeature enum tracks more capability tags (i2c_sda,
        # spi_clk, ...) but those don't appear here — only directional
        # / pull modes do. Pass them through; downstream code can drop
        # unknown values via _safe_enum.
        if isinstance(m, str):
            out.append(m)
    return out


def _detect_platform_type(inner_schema: dict) -> str | None:
    """Infer the platform_type for a NESTED entry from its extends list.

    A nested entry like ``dht.humidity`` extends ``sensor._SENSOR_SCHEMA``
    — that's the signal it represents an entity sub-reading. We surface
    ``"sensor"`` here so the frontend renders it with the sensor base
    fields (name, device_class, ...) on top.
    """
    for ref in inner_schema.get("extends") or []:
        prefix = ref.split(".", 1)[0]
        if prefix in _PLATFORM_DOMAINS:
            return prefix
    return None


def _key_to_label(key: str) -> str:
    """Turn a config-var key into a human-friendly label."""
    return key.replace("_", " ").title()


def _classify_advanced(key: str, *, required: bool, is_structural: bool) -> bool:
    """Decide whether an entry hides behind the "Advanced" toggle.

    Order of precedence:
      1. Structural fields (pins, bus references) — never advanced.
      2. Required fields — never advanced.
      3. ADVANCED_IMPORTANT_KEYS (id, comment) — always advanced.
      4. IMPORTANT_KEYS — never advanced.
      5. ADVANCED_BASE_KEYS — always advanced.
      6. Default: advanced when optional.
    """
    if is_structural:
        return False
    if required:
        return False
    if key in _ADVANCED_IMPORTANT_KEYS:
        return True
    if key in _IMPORTANT_KEYS:
        return False
    if key in _ADVANCED_BASE_KEYS:
        return True
    return True


def _all_inner_advanced(inner: list[dict]) -> bool:
    """Return True iff every inner entry is advanced (else False).

    Empty groups return False so a NESTED parent isn't accidentally
    hidden when its inner entries couldn't be resolved.
    """
    if not inner:
        return False
    return all(e.get("advanced") for e in inner)


def _sort_entries(entries: list[dict]) -> list[dict]:
    """Sort: not-advanced first, then within each group by IMPORTANT_KEY_ORDER."""
    rank = {k: i for i, k in enumerate(_IMPORTANT_KEY_ORDER)}
    fallback = len(_IMPORTANT_KEY_ORDER)

    def sort_key(e: dict) -> tuple[int, int, str]:
        return (
            1 if e.get("advanced") else 0,
            rank.get(e["key"], fallback),
            e["key"],
        )

    return sorted(entries, key=sort_key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_qualified_key(top_key: str) -> tuple[str, str]:
    """Split a schema top-level key into ``(domain, stem)``.

    Schema files key multi-platform components as ``<stem>.<domain>``
    (e.g. ``dht.sensor`` — DHT as a sensor platform). For unqualified
    keys we return ``("", top_key)``.
    """
    if "." in top_key:
        stem, domain = top_key.split(".", 1)
        return domain, stem
    return "", top_key


def _lookup_index_meta(component_id: str, top_key: str, index: SchemaIndex) -> dict:
    """Find the merged-index metadata for a catalog entry.

    Tries the catalog id first (``sensor.dht``, ``wifi``), then the bare
    stem (``dht``). Returns an empty dict if neither matches.
    """
    return (
        index.metadata.get(component_id)
        or index.metadata.get(top_key)
        or index.metadata.get(top_key.split(".", 1)[0])
        or {}
    )


def _docs_type_marker(docs: str) -> str | None:
    """Extract the ``**type**:`` marker from a docs string, if any.

    Handles bare bold (``**boolean**:``) and bracketed-link forms
    (``**[Time](...)**:``). Returns the inner text or None when no
    marker is present.
    """
    if not docs:
        return None
    m = re.match(r"^\*\*\[?([^\]*]+)\]?(?:\([^)]+\))?\*\*\s*[:\-]", docs)
    return m.group(1).strip() if m else None


def _looks_like_boolean_enum(raw: dict) -> bool:
    """Return True iff ``raw['values']`` is exactly ``{true, false}``."""
    values = raw.get("values")
    if not isinstance(values, dict):
        return False
    keys = {str(k).lower() for k in values}
    return keys == {"true", "false"} or keys == {"true", "false", "yes", "no"}


def _looks_like_time_period_default(value: Any) -> bool:
    """Return True iff *value* is a string shaped like a time period."""
    if not isinstance(value, str):
        return False
    return bool(_TIME_PERIOD_DEFAULT.match(value.strip().replace(" ", "")))


def _has_config_schema(section: dict) -> bool:
    """Check whether *section* exposes its own ``CONFIG_SCHEMA``."""
    schemas = section.get("schemas")
    return isinstance(schemas, dict) and "CONFIG_SCHEMA" in schemas


def _strip_anchor(url: str) -> str:
    """Drop ``#anchor`` from a URL to get the bare component-page link."""
    if "#" in url:
        return url.split("#", 1)[0]
    return url


# Hardware acronyms that should retain their canonical capitalisation
# in derived component names. Applied AFTER the default ``str.title()``
# (which gives e.g. "Rc522 Spi") to recover "RC522 SPI".
_ACRONYM_NORMALISATIONS: dict[str, str] = {
    "Adc": "ADC",
    "Dac": "DAC",
    "Bldc": "BLDC",
    "Bme": "BME",
    "I2C": "I²C",
    "I2c": "I²C",
    "Spi": "SPI",
    "Uart": "UART",
    "Ble": "BLE",
    "Pwm": "PWM",
    "Gpio": "GPIO",
    "Rgb": "RGB",
    "Rgbw": "RGBW",
    "Rgbww": "RGBWW",
    "Cwww": "CWWW",
    "Led": "LED",
    "Lcd": "LCD",
    "Oled": "OLED",
    "Tft": "TFT",
    "Usb": "USB",
    "Ota": "OTA",
    "Mqtt": "MQTT",
    "Wifi": "Wi-Fi",
    "Tcp": "TCP",
    "Udp": "UDP",
    "Http": "HTTP",
    "Https": "HTTPS",
    "Url": "URL",
    "Json": "JSON",
    "Pcm": "PCM",
    "Mac": "MAC",
    "Pir": "PIR",
    "Imu": "IMU",
    "Nfc": "NFC",
    "Rfid": "RFID",
    "Pn532": "PN532",
    "Rc522": "RC522",
    "Esp32": "ESP32",
    "Esp8266": "ESP8266",
    "Esphome": "ESPHome",
    "Rp2040": "RP2040",
    "Esp32C3": "ESP32-C3",
    "Esp32S2": "ESP32-S2",
    "Esp32S3": "ESP32-S3",
    "Esp32H2": "ESP32-H2",
    "Esp32C5": "ESP32-C5",
    "Esp32C6": "ESP32-C6",
    "Esp32C61": "ESP32-C61",
    "Esp32P4": "ESP32-P4",
}


def _resolve_name(component_id: str, stem: str, doc_name: str | None) -> str:
    """Produce a human label for the component.

    Preference order:
      1. The link text from the ``See also`` footer (e.g.
         "DHT Temperature+Humidity Sensor").
      2. A title-cased version of the stem with hardware acronyms
         normalised back to their canonical capitalisation.
    """
    if doc_name:
        return doc_name
    name = stem.replace("_", " ").title()
    for k, v in _ACRONYM_NORMALISATIONS.items():
        # Word-boundary replace so "Pwm" -> "PWM" but "Pwms" doesn't
        # accidentally match.
        name = re.sub(rf"\b{re.escape(k)}\b", v, name)
    return name


# Category overrides for non-platform components — matches the legacy
# catalog so existing UI groupings stay stable.
_CATEGORY_OVERRIDES: dict[str, str] = {
    # Core ESPHome infrastructure
    "esphome": "core",
    "wifi": "core",
    "api": "core",
    "ota": "core",
    "logger": "core",
    "mqtt": "core",
    "web_server": "core",
    "captive_portal": "core",
    "safe_mode": "core",
    "time": "core",
    "network": "core",
    # Bus / transport components
    "i2c": "bus",
    "spi": "bus",
    "uart": "bus",
    "one_wire": "bus",
    "modbus": "bus",
    "canbus": "bus",
    # Automation primitives
    "script": "automation",
    "interval": "automation",
    "globals": "automation",
}


def _infer_misc_category(top_key: str) -> str:
    """Best-effort category for non-platform components.

    Looks up ``_CATEGORY_OVERRIDES`` first (curated list mirroring the
    legacy catalog), falls through to ``misc`` for everything else.
    """
    return _CATEGORY_OVERRIDES.get(top_key, "misc")


# ---------------------------------------------------------------------------
# Narrow ESPHome introspection
# ---------------------------------------------------------------------------
#
# The pre-built schema bundle doesn't surface three things we still want:
#
#   - multi_conf: whether a component can appear more than once in YAML
#   - platform_defaults: per-target-platform default values (cv.SplitDefault)
#   - supported_platforms: which target chips a component can be used on
#
# We pull these directly from the installed ``esphome`` package. When
# ``esphome`` isn't available (CI without the dep), introspection is a
# no-op and the catalog ships without those fields populated.

# Target-platform component ids — components named after a chip family
# that act as the "platform" entry in YAML.
_TARGET_PLATFORMS: frozenset[str] = frozenset(
    {
        "esp32",
        "esp8266",
        "rp2040",
        "bk72xx",
        "rtl87xx",
        "ln882x",
        "nrf52",
        "host",
    }
)


def introspect_component(component_id: str) -> dict[str, Any]:
    """Return ``{multi_conf, platform_defaults, supported_platforms, refined_types}``.

    Best-effort: returns an empty dict when ``esphome`` isn't importable
    or the component module can't be loaded.
    """
    if not component_id:
        return {}
    loader = _get_esphome_loader()
    if loader is None:
        return {}
    try:
        manifest = loader.get_component(component_id)
    except Exception:
        return {}
    if manifest is None:
        return {}

    return {
        "multi_conf": bool(getattr(manifest, "multi_conf", False)),
        "is_target_platform": bool(getattr(manifest, "is_target_platform", False)),
        "platform_defaults": _collect_platform_defaults(manifest),
        "refined_types": _collect_refined_types(manifest),
    }


def _get_esphome_loader() -> Any:
    """Lazy import ``esphome.loader``; cache the (success or failure) result."""
    if _ESPHOME_LOADER_CACHE["resolved"]:
        return _ESPHOME_LOADER_CACHE["module"]
    _ESPHOME_LOADER_CACHE["resolved"] = True
    try:
        import esphome.loader as loader

        _ESPHOME_LOADER_CACHE["module"] = loader
        _LOGGER.info("esphome introspection enabled (esphome.loader importable)")
    except Exception:
        _ESPHOME_LOADER_CACHE["module"] = None
        _LOGGER.warning(
            "esphome.loader not importable — multi_conf, platform_defaults, "
            "supported_platforms will be empty"
        )
    return _ESPHOME_LOADER_CACHE["module"]


_ESPHOME_LOADER_CACHE: dict[str, Any] = {"resolved": False, "module": None}


def _is_json_safe(value: Any) -> bool:
    """Return True iff *value* is a primitive JSON-encodable scalar."""
    return isinstance(value, (str, int, float, bool)) or value is None


# Default values for config-entry fields. Anything matching one of
# these is stripped from the serialized JSON so the output stays close
# to the size of the previous mashumaro-based catalog (which omitted
# defaults via ``serialization_strategy``).
_ENTRY_DEFAULTS: dict[str, Any] = {
    "description": None,
    "required": False,
    "default_value": None,
    "platform_defaults": None,
    "options": None,
    "allow_custom_value": False,
    "range": None,
    "multi_value": False,
    "templatable": False,
    "depends_on": None,
    "depends_on_value": None,
    "depends_on_value_not": None,
    "depends_on_component": None,
    "references_component": None,
    "pin_features": [],
    "pin_mode": None,
    "advanced": False,
    "hidden": False,
    "help_link": None,
    "translation_key": None,
    "translation_params": None,
    "config_entries": None,
    "platform_type": None,
}

_COMPONENT_DEFAULTS: dict[str, Any] = {
    "docs_url": "",
    "image_url": "",
    "dependencies": [],
    "multi_conf": False,
    "supported_platforms": [],
    "config_entries": [],
}


def _strip_defaults(component: dict) -> dict:
    """Drop fields equal to their dataclass default to slim the JSON."""
    out: dict[str, Any] = {}
    for k, v in component.items():
        if k in _COMPONENT_DEFAULTS and v == _COMPONENT_DEFAULTS[k]:
            continue
        if k == "config_entries" and v:
            out[k] = [_strip_entry_defaults(e) for e in v]
            continue
        out[k] = v
    return out


def _strip_entry_defaults(entry: dict) -> dict:
    """Recursive variant of ``_strip_defaults`` for ConfigEntry dicts."""
    out: dict[str, Any] = {}
    for k, v in entry.items():
        if k in _ENTRY_DEFAULTS and v == _ENTRY_DEFAULTS[k]:
            continue
        if k == "config_entries" and v:
            out[k] = [_strip_entry_defaults(e) for e in v]
            continue
        out[k] = v
    return out


def _collect_platform_defaults(manifest: Any) -> dict[tuple[str, ...], dict[str, Any]]:
    """Walk the live ``CONFIG_SCHEMA`` for ``cv.SplitDefault`` keys.

    Returns ``{key_path: {platform: default_value}}`` keyed by tuple
    paths so nested fields can be looked up unambiguously. When the
    component has no schema (rare) or voluptuous isn't importable,
    returns an empty dict.
    """
    schema = getattr(manifest, "config_schema", None)
    if schema is None:
        return {}
    try:
        import voluptuous as vol
    except Exception:
        return {}

    out: dict[tuple[str, ...], dict[str, Any]] = {}
    visited: set[int] = set()

    def unwrap_to_dict(node: Any) -> dict | None:
        """Best-effort: peel ``vol.Schema`` / ``vol.All`` until we hit a dict."""
        for _ in range(8):
            if isinstance(node, dict):
                return node
            if hasattr(node, "schema"):
                node = node.schema
                continue
            inner = getattr(node, "validators", None)
            if inner:
                next_node = None
                for v in inner:
                    if isinstance(v, dict) or hasattr(v, "schema"):
                        next_node = v
                        break
                if next_node is None:
                    return None
                node = next_node
                continue
            return None
        return None

    def walk(node: Any, path: tuple[str, ...], depth: int) -> None:
        if depth > 6:
            return
        candidate = unwrap_to_dict(node)
        if candidate is None:
            return
        if id(candidate) in visited:
            return
        visited.add(id(candidate))

        for key, val in candidate.items():
            key_name = key.schema if hasattr(key, "schema") else str(key)
            sub_path = (*path, key_name)
            if isinstance(key, vol.Optional):
                factories = getattr(key, "_defaults", None)
                if isinstance(factories, dict):
                    per_platform: dict[str, Any] = {}
                    for plat, factory in factories.items():
                        try:
                            value = factory() if callable(factory) else factory
                        except Exception:  # noqa: S112 — best-effort default extraction
                            continue
                        if value is vol.UNDEFINED:
                            continue
                        if not _is_json_safe(value):
                            continue
                        per_platform[str(plat)] = value
                    if per_platform:
                        out[sub_path] = per_platform
            walk(val, sub_path, depth + 1)

    try:
        walk(schema, (), 0)
    except Exception:
        return {}
    return out


def _collect_refined_types(manifest: Any) -> dict[tuple[str, ...], str]:
    """Walk the live ``CONFIG_SCHEMA`` to recover types the schema lost.

    The pre-built schema collapses many ``cv.boolean`` / ``cv.float_`` /
    ``cv.icon`` / ``cv.lambda_`` validators into bare strings. By
    inspecting the actual voluptuous validators we can promote those
    fields back to the right type. Returns ``{key_path: type_name}``.
    """
    schema = getattr(manifest, "config_schema", None)
    if schema is None:
        return {}
    try:
        from esphome import config_validation as cv
    except Exception:
        return {}

    # Map runtime validator identities / names to our type strings. The
    # schema bundle already gets ``cv.string`` and ``cv.int_`` right via
    # explicit ``type:`` markers; we focus on the cases where the
    # bundle silently emits no type at all. Identity is keyed by
    # ``id()`` because some voluptuous validators (notably _Schema
    # subclasses) override __hash__ to be unhashable.
    by_identity: dict[int, str] = {}
    by_name: dict[str, str] = {}

    def add(name: str, type_str: str, *attrs: str) -> None:
        by_name[name] = type_str
        for a in attrs:
            obj = getattr(cv, a, None)
            if obj is not None:
                by_identity[id(obj)] = type_str

    add("boolean", "boolean", "boolean")
    add("float_", "float", "float_", "positive_float", "negative_float")
    add("float_range", "float", "float_range")
    add("frequency", "float", "frequency")
    add("icon", "icon", "icon")
    add("lambda_", "lambda", "lambda_")
    add("returning_lambda", "lambda", "returning_lambda")
    add("mac_address", "mac_address", "mac_address")
    add("color_temperature", "string", "color_temperature")

    out: dict[tuple[str, ...], str] = {}
    visited: set[int] = set()

    def unwrap_to_dict(node: Any) -> dict | None:
        for _ in range(8):
            if isinstance(node, dict):
                return node
            if hasattr(node, "schema"):
                node = node.schema
                continue
            inner = getattr(node, "validators", None)
            if inner:
                next_node = None
                for v in inner:
                    if isinstance(v, dict) or hasattr(v, "schema"):
                        next_node = v
                        break
                if next_node is None:
                    return None
                node = next_node
                continue
            return None
        return None

    def classify(validator: Any) -> str | None:
        if id(validator) in by_identity:
            return by_identity[id(validator)]
        # Some validators are wrapped (vol.All chains or partials);
        # peel down to find the inner.
        inner = getattr(validator, "validators", None)
        if inner:
            for v in inner:
                t = classify(v)
                if t is not None:
                    return t
        # Fall back to name-based matching for closures and partials
        # that lose identity but keep the name.
        name = (
            getattr(validator, "__name__", None) or getattr(validator, "__qualname__", None) or ""
        ).lower()
        for k, t in by_name.items():
            if k in name:
                return t
        return None

    def walk(node: Any, path: tuple[str, ...], depth: int) -> None:
        if depth > 6:
            return
        candidate = unwrap_to_dict(node)
        if candidate is None:
            return
        if id(candidate) in visited:
            return
        visited.add(id(candidate))
        for key, val in candidate.items():
            key_name = key.schema if hasattr(key, "schema") else str(key)
            sub_path = (*path, key_name)
            t = classify(val)
            if t is not None:
                out[sub_path] = t
            walk(val, sub_path, depth + 1)

    try:
        walk(schema, (), 0)
    except Exception:
        return {}
    return out


def _apply_refined_types(
    entries: list[dict],
    refined: dict[tuple[str, ...], str],
) -> None:
    """Promote entry types from string → boolean/float/... where known.

    Only acts on entries currently typed ``string`` so we don't
    override the schema's explicit type assignments.
    """
    if not refined:
        return

    def walk(items: list[dict], path: tuple[str, ...]) -> None:
        for entry in items:
            sub_path = (*path, entry["key"])
            new_type = refined.get(sub_path)
            if new_type and entry.get("type") == "string":
                entry["type"] = new_type
            inner = entry.get("config_entries")
            if inner:
                walk(inner, sub_path)

    walk(entries, ())


def _apply_platform_defaults(
    entries: list[dict],
    platform_defaults: dict[tuple[str, ...], dict[str, Any]],
) -> None:
    """Layer ``platform_defaults`` from introspection onto matching entries."""
    if not platform_defaults:
        return

    def walk(items: list[dict], path: tuple[str, ...]) -> None:
        for entry in items:
            sub_path = (*path, entry["key"])
            pd = platform_defaults.get(sub_path)
            if pd:
                entry["platform_defaults"] = pd
            inner = entry.get("config_entries")
            if inner:
                walk(inner, sub_path)

    walk(entries, ())


def _derive_supported_platforms(
    component_id: str,
    dependencies: list[str],
    introspection: dict[str, Any],
) -> list[str]:
    """Return the list of target chips this component runs on.

    Target-platform components (``esp32``, ``rp2040``, ...) report
    themselves. Otherwise, dependencies that match ``_TARGET_PLATFORMS``
    are surfaced — ``esp32_ble_tracker`` depends on ``esp32`` so we
    return ``["esp32"]``; most components have no platform-specific
    deps and return ``[]`` (treated as "all platforms").
    """
    if introspection.get("is_target_platform"):
        return [component_id]
    return [d for d in dependencies if d in _TARGET_PLATFORMS]


if __name__ == "__main__":
    sys.exit(main())
