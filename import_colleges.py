#!/usr/bin/env python3
"""
Bulk-create College / College Campus / College Building / College Address
items on the Atlantic Commons Wikibase (https://atlanticcommons.ca).

The item shapes mirror four existing example items on the wiki:

    Q1042  Holland College                        (college)
    Q1041  Summerside Waterfront Campus            (campus)
    Q1040  Summerside Waterfront Campus Building   (building)
    Q1039  98 Water Street Summerside, PE          (address)

Schema discovered from those items:

    Property P1  "instance of"              -> item   (used on every item below)
    Property P15 "forward sortation area"   -> item   (on address, optional)
    Property P16 "postal code"              -> string (on address)
    Property P17 "street number"            -> string (on address)
    Property P18 "street name"              -> string (on address)
    Property P19 "municipality"             -> item   (on address)
    Property P20 "province"                 -> item   (on address)
    Property P21 "address"                  -> item   (on building, -> address item)
    Property P22 "building"                 -> item   (on campus, -> building item)
    Property P23 "has campus"               -> item   (on college, -> campus item)

    Q1036 "college", Q1038 "campus", Q1034 "building", Q1032 "address"
    are the "instance of" (P1) values for each item type.

Municipality / province / forward-sortation-area items (e.g. "Summerside",
"Prince Edward Island", "C1N") are treated as pre-existing reference data:
this script looks them up by label with wbsearchentities rather than
creating new ones. If a lookup fails, fix the input data or create that
reference item by hand first -- it is out of scope for this script.

Usage:

    Create a .env file (see .env.example) with:

        WIKIBASE_USERNAME=YourBotName@your-bot-password-name
        WIKIBASE_PASSWORD=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    python3 import_colleges.py --data example_data.json --dry-run
    python3 import_colleges.py --data example_data.json --output result.json

Input data is a JSON list of colleges; see example_data.json for the shape.
Any "qid" field left null/omitted means "create a new item"; set it to an
existing QID (e.g. "Q1042") to reuse/extend an existing college, campus,
building, or address instead of creating a duplicate. When "qid" is set,
none of that item's other fields (label, description, street_number, etc.)
need to be present -- they're only used to create a new item, so they're
ignored once "qid" points at one that already exists.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any, Optional

from dotenv import load_dotenv

from wikibase_client import WikibaseClient, item_claim, string_claim

load_dotenv()

# -- schema discovered from Q1039-Q1042 --------------------------------

PROP_INSTANCE_OF = "P1"
PROP_FSA = "P15"
PROP_POSTAL_CODE = "P16"
PROP_STREET_NUMBER = "P17"
PROP_STREET_NAME = "P18"
PROP_MUNICIPALITY = "P19"
PROP_PROVINCE = "P20"
PROP_ADDRESS = "P21"
PROP_BUILDING = "P22"
PROP_HAS_CAMPUS = "P23"

TYPE_COLLEGE = "Q1036"
TYPE_CAMPUS = "Q1038"
TYPE_BUILDING = "Q1034"
TYPE_ADDRESS = "Q1032"


class DryRunClient:
    """Stand-in for WikibaseClient that performs real (read-only) lookups
    but only prints the writes it would make."""

    def __init__(self, api_url: str):
        self._counter = 0
        self._reader = WikibaseClient(api_url)

    def search_entity(self, label: str, language: str = "en", entity_type: str = "item") -> Optional[str]:
        qid = self._reader.search_entity(label, language=language, entity_type=entity_type)
        print(f"  [dry-run] searched for existing item labelled {label!r} -> {qid}")
        return qid

    def create_item(self, labels, descriptions=None, claims=None) -> str:
        self._counter += 1
        fake_qid = f"Q<new-{self._counter}>"
        print(f"  [dry-run] would create item {fake_qid}: labels={labels} descriptions={descriptions}")
        for c in claims or []:
            print(f"      + claim {c}")
        return fake_qid

    def add_statement(self, qid: str, property_id: str, value: Any, value_type: str) -> str:
        print(f"  [dry-run] would add statement on {qid}: {property_id} -> {value} ({value_type})")
        return "dry-run-claim"

    def sleep_between_writes(self, seconds: float = 1.0) -> None:
        pass


def resolve_reference(client, cache: dict[str, str], value: str, kind: str) -> str:
    """Resolve a municipality/province/FSA reference: a literal QID or a label to search for."""
    if value.startswith("Q") and value[1:].isdigit():
        return value
    if value in cache:
        return cache[value]
    qid = client.search_entity(value)
    if qid is None:
        raise SystemExit(
            f"Could not find an existing {kind} item labelled {value!r}. "
            f"Create it manually first, or pass its QID directly in the input data."
        )
    cache[value] = qid
    return qid


def generate_address_description(spec: dict) -> str:
    """Build a default address description, e.g. 'address in Summerside, Prince
    Edward Island located on Water Street', from the municipality/province/
    street_name fields when the input data doesn't supply one."""
    location = ", ".join(p for p in (spec.get("municipality"), spec.get("province")) if p)
    description = f"address in {location}" if location else "address"
    if spec.get("street_name"):
        description += f" located on {spec['street_name']}"
    return description


def create_address(client, cache: dict[str, str], spec: dict) -> tuple[str, bool]:
    if spec.get("qid"):
        return spec["qid"], False

    municipality_qid = resolve_reference(client, cache, spec["municipality"], "municipality")
    province_qid = resolve_reference(client, cache, spec["province"], "province")

    claims = [item_claim(PROP_INSTANCE_OF, TYPE_ADDRESS)]
    if spec.get("street_number"):
        claims.append(string_claim(PROP_STREET_NUMBER, spec["street_number"]))
    if spec.get("street_name"):
        claims.append(string_claim(PROP_STREET_NAME, spec["street_name"]))
    if spec.get("postal_code"):
        claims.append(string_claim(PROP_POSTAL_CODE, spec["postal_code"]))
    claims.append(item_claim(PROP_MUNICIPALITY, municipality_qid))
    claims.append(item_claim(PROP_PROVINCE, province_qid))
    if spec.get("postal_code"):
        fsa = spec["postal_code"][:3]
        fsa_qid = resolve_reference(client, cache, fsa, "forward sortation area")
        claims.append(item_claim(PROP_FSA, fsa_qid))

    description = spec.get("description") or generate_address_description(spec)
    qid = client.create_item(
        labels={"en": spec["label"]},
        descriptions={"en": description},
        claims=claims,
    )
    client.sleep_between_writes()
    return qid, True


def create_simple_item(client, spec: dict, type_qid: str) -> tuple[str, bool]:
    """Create a college/campus/building item carrying only the P1 instance-of claim."""
    if spec.get("qid"):
        return spec["qid"], False

    qid = client.create_item(
        labels={"en": spec["label"]},
        descriptions={"en": spec["description"]} if spec.get("description") else None,
        claims=[item_claim(PROP_INSTANCE_OF, type_qid)],
    )
    client.sleep_between_writes()
    return qid, True


def link(client, parent_qid: str, parent_created: bool, prop: str, child_qid: str, child_created: bool) -> None:
    """Add parent -> child statement, unless both sides pre-existed (link presumed already present)."""
    if not (parent_created or child_created):
        return
    client.add_statement(parent_qid, prop, child_qid, "wikibase-item")
    client.sleep_between_writes()


def process_college(client, cache: dict[str, str], college_spec: dict) -> dict:
    college_qid, college_created = create_simple_item(client, college_spec, TYPE_COLLEGE)
    print(f"College {college_spec.get('label', college_qid)!r}: {college_qid} ({'created' if college_created else 'existing'})")

    result = {"qid": college_qid, "created": college_created, "campuses": []}

    for campus_spec in college_spec.get("campuses", []):
        campus_qid, campus_created = create_simple_item(client, campus_spec, TYPE_CAMPUS)
        print(f"  Campus {campus_spec.get('label', campus_qid)!r}: {campus_qid} ({'created' if campus_created else 'existing'})")
        link(client, college_qid, college_created, PROP_HAS_CAMPUS, campus_qid, campus_created)

        campus_result = {"qid": campus_qid, "created": campus_created, "buildings": []}

        for building_spec in campus_spec.get("buildings", []):
            building_qid, building_created = create_simple_item(client, building_spec, TYPE_BUILDING)
            print(
                f"    Building {building_spec.get('label', building_qid)!r}: {building_qid} "
                f"({'created' if building_created else 'existing'})"
            )
            link(client, campus_qid, campus_created, PROP_BUILDING, building_qid, building_created)

            building_result = {"qid": building_qid, "created": building_created, "address": None}

            address_spec = building_spec.get("address")
            if address_spec:
                address_qid, address_created = create_address(client, cache, address_spec)
                print(
                    f"      Address {address_spec.get('label', address_qid)!r}: {address_qid} "
                    f"({'created' if address_created else 'existing'})"
                )
                link(client, building_qid, building_created, PROP_ADDRESS, address_qid, address_created)
                building_result["address"] = {"qid": address_qid, "created": address_created}

            campus_result["buildings"].append(building_result)

        result["campuses"].append(campus_result)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True, help="Path to a JSON file describing colleges to import")
    parser.add_argument("--api-url", default="https://atlanticcommons.ca/w/api.php")
    parser.add_argument("--username", default=os.environ.get("WIKIBASE_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("WIKIBASE_PASSWORD"))
    parser.add_argument("--output", help="Where to write a JSON summary of created/reused QIDs")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without writing to the wiki")
    parser.add_argument(
        "--no-bot-flag",
        action="store_true",
        help=(
            "Don't mark edits as bot edits. Use this for small test runs with a regular "
            "(non-bot-password) account that doesn't have bot rights on the wiki."
        ),
    )
    args = parser.parse_args()

    with open(args.data, encoding="utf-8") as f:
        colleges = json.load(f)

    if args.dry_run:
        client = DryRunClient(args.api_url)
    else:
        if not args.username:
            args.username = input("Wikibase username (e.g. Bot@bot-password-name, or a regular username): ")
        if not args.password:
            args.password = getpass.getpass("Wikibase password: ")
        client = WikibaseClient(args.api_url, bot=not args.no_bot_flag)
        client.login(args.username, args.password)

    cache: dict[str, str] = {}
    results = [process_college(client, cache, spec) for spec in colleges]

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"\nWrote summary to {args.output}")


if __name__ == "__main__":
    main()
