"""
Minimal MediaWiki/Wikibase API client used to create and link items on
https://atlanticcommons.ca (a Wikibase instance).

Handles bot-password login, CSRF tokens, item creation (wbeditentity),
label/description-based entity search (wbsearchentities), and adding a
single statement to an existing item (wbcreateclaim).
"""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

USER_AGENT = "atlanticcommons-college-import/1.0 (https://atlanticcommons.ca)"


class WikibaseError(RuntimeError):
    pass


class WikibaseClient:
    def __init__(self, api_url: str = "https://atlanticcommons.ca/w/api.php", bot: bool = True):
        self.api_url = api_url
        self.bot = bot
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._csrf_token: Optional[str] = None

    # -- low level -----------------------------------------------------

    def _get(self, **params: Any) -> dict:
        params["format"] = "json"
        resp = self.session.get(self.api_url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, **data: Any) -> dict:
        data["format"] = "json"
        resp = self.session.post(self.api_url, data=data, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise WikibaseError(body["error"])
        return body

    # -- auth ------------------------------------------------------------

    def login(self, username: str, password: str) -> None:
        """Log in with a bot password (Special:BotPasswords)."""
        token = self._get(action="query", meta="tokens", type="login")["query"]["tokens"]["logintoken"]
        result = self._post(
            action="login",
            lgname=username,
            lgpassword=password,
            lgtoken=token,
        )
        status = result.get("login", {}).get("result")
        if status != "Success":
            raise WikibaseError(f"Login failed: {result}")

    def _csrf(self) -> str:
        if not self._csrf_token:
            self._csrf_token = self._get(action="query", meta="tokens")["query"]["tokens"]["csrftoken"]
        return self._csrf_token

    # -- entities ----------------------------------------------------------

    def search_entity(self, label: str, language: str = "en", entity_type: str = "item") -> Optional[str]:
        """Return the QID of an existing item whose label exactly matches `label`, or None."""
        result = self._get(
            action="wbsearchentities",
            search=label,
            language=language,
            type=entity_type,
            limit=10,
        )
        for hit in result.get("search", []):
            if hit.get("label", "").strip().lower() == label.strip().lower():
                return hit["id"]
        return None

    def get_entity(self, qid: str) -> dict:
        result = self._get(action="wbgetentities", ids=qid)
        return result["entities"][qid]

    def create_item(
        self,
        labels: dict[str, str],
        descriptions: Optional[dict[str, str]] = None,
        claims: Optional[list[dict]] = None,
    ) -> str:
        """Create a new item. Returns the new QID."""
        data: dict[str, Any] = {
            "labels": {lang: {"language": lang, "value": val} for lang, val in labels.items()}
        }
        if descriptions:
            data["descriptions"] = {
                lang: {"language": lang, "value": val} for lang, val in descriptions.items()
            }
        if claims:
            data["claims"] = claims

        params: dict[str, Any] = dict(
            action="wbeditentity",
            new="item",
            data=_json_dumps(data),
            token=self._csrf(),
        )
        if self.bot:
            params["bot"] = 1
        result = self._post(**params)
        return result["entity"]["id"]

    def add_statement(self, qid: str, property_id: str, value: Any, value_type: str) -> str:
        """
        Add a single statement to an existing item.

        value_type is one of: "wikibase-item" (value = target QID string),
        or "string" (value = plain string).
        """
        if value_type == "wikibase-item":
            snak_value = _json_dumps({"entity-type": "item", "id": value})
        elif value_type == "string":
            snak_value = _json_dumps(value)
        else:
            raise ValueError(f"Unsupported value_type: {value_type}")

        params: dict[str, Any] = dict(
            action="wbcreateclaim",
            entity=qid,
            property=property_id,
            snaktype="value",
            value=snak_value,
            token=self._csrf(),
        )
        if self.bot:
            params["bot"] = 1
        result = self._post(**params)
        return result["claim"]["id"]

    def sleep_between_writes(self, seconds: float = 1.0) -> None:
        time.sleep(seconds)


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj)


def item_claim(property_id: str, target_qid: str) -> dict:
    """Build a wbeditentity claim entry pointing at another item."""
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": property_id,
            "datavalue": {
                "value": {"entity-type": "item", "id": target_qid},
                "type": "wikibase-entityid",
            },
        },
        "type": "statement",
        "rank": "normal",
    }


def string_claim(property_id: str, value: str) -> dict:
    """Build a wbeditentity claim entry holding a plain string value."""
    return {
        "mainsnak": {
            "snaktype": "value",
            "property": property_id,
            "datavalue": {"value": value, "type": "string"},
        },
        "type": "statement",
        "rank": "normal",
    }
