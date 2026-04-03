#!/usr/bin/env python3

import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 30
HTTP_RETRY_DELAYS = (1, 2, 4)
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class NotAuthenticatedError(Exception):
    """Raised when the Amazon session has expired and login is required."""


class AlexaShoppingList:
    def __init__(self, amazon_url: str = "amazon.co.uk", cookies_path: str = ""):
        self.amazon_url = amazon_url
        self.cookies_path = cookies_path
        self.is_authenticated = False

    # ============================================================
    # Helpers

    def _get_file_location(self):
        return os.path.dirname(os.path.realpath(__file__))

    def _cookie_cache_path(self):
        if self.cookies_path:
            return os.path.join(self.cookies_path, "cookies.json")
        return os.path.join(self._get_file_location(), "cookies.json")

    def _read_cookie_cache(self):
        if not os.path.exists(self._cookie_cache_path()):
            return []

        try:
            with open(self._cookie_cache_path(), "r", encoding="utf-8") as file:
                cookies = json.load(file)
                if isinstance(cookies, list):
                    return cookies
        except Exception:
            pass

        return []

    def _cookie_debug_summary(self):
        cookies = self._read_cookie_cache()
        cookie_names = sorted(
            cookie.get("name")
            for cookie in cookies
            if isinstance(cookie, dict) and cookie.get("name")
        )
        return {
            "cookie_path": self._cookie_cache_path(),
            "cookie_count": len(cookie_names),
            "cookie_names": cookie_names,
        }

    def _response_debug_summary(self, response):
        body = response.get("body") or ""
        compact_body = " ".join(body.split())[:300]
        return {
            "ok": response.get("ok"),
            "status": response.get("status"),
            "json_type": type(response.get("json")).__name__ if response.get("json") is not None else None,
            "body_snippet": compact_body,
        }

    def _http_cookie_header(self):
        cookie_header = "; ".join(
            f"{cookie.get('name')}={cookie.get('value')}"
            for cookie in self._read_cookie_cache()
            if cookie.get("name") and cookie.get("value") is not None
        )

        if not cookie_header:
            raise RuntimeError("No cookies available for HTTP request")

        return cookie_header

    def _http_request_json(self, url: str, method: str = "GET", payload=None):
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "cookie": self._http_cookie_header(),
            "referer": f"https://www.{self.amazon_url}/alexaquantum/sp/alexaShoppingList?ref=nav_asl",
            "user-agent": HTTP_USER_AGENT,
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )

        last_error = None

        for attempt, retry_delay in enumerate((0,) + HTTP_RETRY_DELAYS, start=1):
            try:
                with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                    body = response.read().decode("utf-8")
                    parsed = None
                    if body:
                        try:
                            parsed = json.loads(body)
                        except json.JSONDecodeError:
                            parsed = None
                    return {
                        "ok": True,
                        "status": response.status,
                        "body": body,
                        "json": parsed,
                    }
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                parsed = None
                if body:
                    try:
                        parsed = json.loads(body)
                    except json.JSONDecodeError:
                        parsed = None

                is_transient_http_error = error.code in (429, 500, 502, 503, 504)
                if is_transient_http_error and attempt <= len(HTTP_RETRY_DELAYS):
                    logger.warning(
                        "Transient Alexa HTTP error during %s %s: status=%s, retrying in %ss",
                        method,
                        url,
                        error.code,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue

                return {
                    "ok": False,
                    "status": error.code,
                    "body": body,
                    "json": parsed,
                }
            except (
                http.client.IncompleteRead,
                TimeoutError,
                urllib.error.URLError,
                ConnectionError,
            ) as error:
                last_error = error
                if attempt <= len(HTTP_RETRY_DELAYS):
                    logger.warning(
                        "Transient Alexa network error during %s %s: %s, retrying in %ss",
                        method,
                        url,
                        error,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue
                raise RuntimeError(f"Amazon HTTP request failed after retries: {error}") from error

        raise RuntimeError(f"Amazon HTTP request failed after retries: {last_error}")

    def _ensure_authenticated_response(self, response, action: str):
        status = response.get("status")
        body = response.get("body") or ""
        if status == 401 or "AuthenticationFailure" in body:
            self.is_authenticated = False
            logger.warning(
                "Amazon auth rejected during %s: %s | cookies=%s",
                action,
                json.dumps(self._response_debug_summary(response), ensure_ascii=False),
                json.dumps(self._cookie_debug_summary(), ensure_ascii=False),
            )
            raise NotAuthenticatedError(f"Amazon authentication required during {action}")

        if response.get("ok"):
            self.is_authenticated = True
            return

        raise RuntimeError(
            f"Amazon {action} failed: status={status}, body={body[:200]}"
        )

    def _http_get_list_items_json(self):
        api_url = f"https://www.{self.amazon_url}/alexashoppinglists/api/getlistitems"
        response = self._http_request_json(api_url, method="GET")
        self._ensure_authenticated_response(response, "getlistitems")
        if not isinstance(response.get("json"), dict):
            raise RuntimeError("Invalid getlistitems HTTP response")
        return response["json"]

    def _http_requires_login(self):
        api_url = f"https://www.{self.amazon_url}/alexashoppinglists/api/getlistitems"
        response = self._http_request_json(api_url, method="GET")

        if response.get("ok") and isinstance(response.get("json"), dict):
            self.is_authenticated = True
            return False

        status = response.get("status")
        body = response.get("body") or ""
        if status == 401 or "AuthenticationFailure" in body:
            self.is_authenticated = False
            logger.warning(
                "Amazon auth check determined login is required: %s | cookies=%s",
                json.dumps(self._response_debug_summary(response), ensure_ascii=False),
                json.dumps(self._cookie_debug_summary(), ensure_ascii=False),
            )
            return True

        logger.warning(
            "Amazon auth check returned an unexpected response: %s | cookies=%s",
            json.dumps(self._response_debug_summary(response), ensure_ascii=False),
            json.dumps(self._cookie_debug_summary(), ensure_ascii=False),
        )
        raise RuntimeError(
            f"Unexpected auth check response: status={status}, body={body[:200]}"
        )

    def _normalize_http_list_payload(self, payload):
        normalized = []

        if not isinstance(payload, dict):
            return normalized

        for list_payload in payload.values():
            if not isinstance(list_payload, dict):
                continue

            list_info = list_payload.get("listInfo", {})
            list_id = list_info.get("listId")
            default_list = bool(list_info.get("defaultList"))
            if not default_list:
                continue

            for item in list_payload.get("listItems", []):
                if not isinstance(item, dict):
                    continue

                item_id = item.get("id")
                item_name = item.get("value")
                if not item_id or not item_name:
                    continue

                normalized.append(
                    {
                        "id": item_id,
                        "name": item_name,
                        "complete": bool(item.get("completed", False)),
                        "createdDateTime": item.get("createdDateTime"),
                        "updatedDateTime": item.get("updatedDateTime"),
                        "version": item.get("version"),
                        "listId": item.get("listId") or list_id,
                        "defaultList": default_list,
                    }
                )

        normalized.sort(
            key=lambda item: (
                0 if not item.get("complete") else 1,
                int(item.get("createdDateTime") or 0),
                int(item.get("updatedDateTime") or 0),
                item.get("id") or "",
            )
        )
        return normalized

    def _get_alexa_list_http(self):
        return self._normalize_http_list_payload(self._http_get_list_items_json())

    def _compact_items_for_log(self, items):
        compact = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            compact.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "complete": bool(item.get("complete", False)),
                }
            )
        return compact

    def _http_default_list_payload(self):
        payload = self._http_get_list_items_json()
        for list_payload in payload.values():
            list_info = list_payload.get("listInfo", {})
            if list_info.get("defaultList"):
                return list_payload
        raise RuntimeError("Default Alexa shopping list not found in getlistitems response")

    def _http_default_list_id(self):
        list_payload = self._http_default_list_payload()
        list_info = list_payload.get("listInfo", {})
        list_id = list_info.get("listId")
        if not list_id:
            raise RuntimeError("Default Alexa shopping listId missing")
        return list_id

    def _http_find_item_by_id(self, list_payload, item_id: str):
        for item in list_payload.get("listItems", []):
            if item.get("id") == item_id:
                return item
        return None

    def _http_find_list_item(self, list_payload, value: str, completed=None, prefer_latest=False):
        candidates = []
        for item in list_payload.get("listItems", []):
            if item.get("value") != value:
                continue
            if completed is not None and bool(item.get("completed", False)) != completed:
                continue
            candidates.append(item)

        if not candidates:
            return None

        key_fn = lambda item: (
            int(item.get("createdDateTime", 0) or 0),
            int(item.get("updatedDateTime", 0) or 0),
            item.get("id") or "",
        )
        return max(candidates, key=key_fn) if prefer_latest else min(candidates, key=key_fn)

    def _http_add_list_item(self, item: str):
        list_id = self._http_default_list_id()
        add_url = f"https://www.{self.amazon_url}/alexashoppinglists/api/addlistitem/{list_id}"
        response = self._http_request_json(
            add_url,
            method="POST",
            payload={
                "value": item,
                "listItemMetadata": [],
            },
        )
        self._ensure_authenticated_response(response, "addlistitem")
        if isinstance(response.get("json"), dict):
            item_json = response["json"]
            return {
                "id": item_json.get("id"),
                "name": item_json.get("value"),
                "complete": bool(item_json.get("completed", False)),
                "createdDateTime": item_json.get("createdDateTime"),
                "updatedDateTime": item_json.get("updatedDateTime"),
                "version": item_json.get("version"),
                "listId": item_json.get("listId"),
                "defaultList": True,
            }
        return None

    def _http_update_list_item(self, old: str, new: str, alexa_id: str = None):
        list_payload = self._http_default_list_payload()
        if alexa_id:
            current_item = self._http_find_item_by_id(list_payload, alexa_id)
            if current_item is not None and bool(current_item.get("completed", False)):
                current_item = None
        else:
            current_item = self._http_find_list_item(list_payload, old, completed=False, prefer_latest=False)

        if current_item is None:
            return False

        update_payload = dict(current_item)
        update_payload["value"] = new
        update_url = f"https://www.{self.amazon_url}/alexashoppinglists/api/updatelistitem"
        response = self._http_request_json(update_url, method="PUT", payload=update_payload)
        self._ensure_authenticated_response(response, "updatelistitem")
        return True

    def _http_complete_list_item(self, item: str, alexa_id: str = None):
        list_payload = self._http_default_list_payload()
        if alexa_id:
            current_item = self._http_find_item_by_id(list_payload, alexa_id)
            if current_item is not None and bool(current_item.get("completed", False)):
                current_item = None
        else:
            current_item = self._http_find_list_item(list_payload, item, completed=False, prefer_latest=False)

        if current_item is None:
            return False

        update_payload = dict(current_item)
        update_payload["completed"] = True
        update_url = f"https://www.{self.amazon_url}/alexashoppinglists/api/updatelistitem"
        response = self._http_request_json(update_url, method="PUT", payload=update_payload)
        self._ensure_authenticated_response(response, "complete list item")
        return True

    def _http_remove_list_item(self, item: str):
        list_payload = self._http_default_list_payload()
        current_item = self._http_find_list_item(list_payload, item, completed=False, prefer_latest=False)
        if current_item is None:
            return False

        delete_url = f"https://www.{self.amazon_url}/alexashoppinglists/api/deletelistitem"
        response = self._http_request_json(delete_url, method="DELETE", payload=current_item)
        self._ensure_authenticated_response(response, "deletelistitem")
        return True

    # ============================================================
    # Authentication

    def requires_login(self):
        return self._http_requires_login()

    # ============================================================
    # Alexa lists

    def get_alexa_list(self, refresh: bool = True):
        del refresh
        alexa_items = self._get_alexa_list_http()
        logger.info("Alexa list read via HTTP API")
        return alexa_items

    def complete_alexa_list_item(self, item: str, alexa_id: str = None):
        completed = self._http_complete_list_item(item, alexa_id=alexa_id)
        if not completed:
            return self.get_alexa_list(False)

        refreshed = self.get_alexa_list(False)
        logger.info(
            "Alexa HTTP complete result for '%s': %s",
            item,
            json.dumps(self._compact_items_for_log(refreshed), ensure_ascii=False),
        )
        return refreshed

    def add_alexa_list_item(self, item: str, include_details: bool = False):
        logger.info("Alexa add requested: %s", item)
        added_item = self._http_add_list_item(item)
        refreshed = self.get_alexa_list(False)
        logger.info(
            "Alexa add result for '%s': %s",
            item,
            json.dumps(self._compact_items_for_log(refreshed), ensure_ascii=False),
        )
        if include_details:
            return {
                "list": refreshed,
                "added_items": [added_item] if added_item is not None else [],
            }
        return refreshed

    def update_alexa_list_item(self, old: str, new: str, alexa_id: str = None):
        self._http_update_list_item(old, new, alexa_id=alexa_id)
        refreshed = self.get_alexa_list(False)
        logger.info(
            "Alexa HTTP update result for '%s' -> '%s': %s",
            old,
            new,
            json.dumps(self._compact_items_for_log(refreshed), ensure_ascii=False),
        )
        return refreshed

    def remove_alexa_list_item(self, item: str):
        self._http_remove_list_item(item)
        refreshed = self.get_alexa_list(False)
        logger.info(
            "Alexa HTTP delete result for '%s': %s",
            item,
            json.dumps(self._compact_items_for_log(refreshed), ensure_ascii=False),
        )
        return refreshed

    def bulk_apply_alexa_list_changes(
        self,
        add_items=None,
        remove_items=None,
        update_items=None,
        complete_items=None,
        include_details: bool = False,
    ):
        add_items = add_items or []
        remove_items = remove_items or []
        update_items = update_items or []
        complete_items = complete_items or []

        logger.info(
            "Alexa bulk apply requested: %s",
            json.dumps(
                {
                    "add_items": add_items,
                    "remove_items": remove_items,
                    "update_items": update_items,
                    "complete_items": complete_items,
                },
                ensure_ascii=False,
            ),
        )

        added_items_result = []

        for item in add_items:
            logger.info("Alexa bulk add item: %s", item)
            added_item = self._http_add_list_item(item)
            if added_item is not None:
                added_items_result.append(added_item)

        for item in remove_items:
            logger.info("Alexa bulk remove item: %s", item)
            self._http_remove_list_item(item)

        for update in update_items:
            logger.info("Alexa bulk update item: %s", json.dumps(update, ensure_ascii=False))
            self._http_update_list_item(
                update["old"],
                update["new"],
                alexa_id=update.get("alexa_id"),
            )

        for item in complete_items:
            logger.info("Alexa bulk complete item: %s", item)
            if isinstance(item, dict):
                self._http_complete_list_item(
                    item.get("name") or "",
                    alexa_id=item.get("alexa_id"),
                )
            else:
                self._http_complete_list_item(item)

        refreshed = self.get_alexa_list(False)
        logger.info(
            "Alexa bulk apply result: %s",
            json.dumps(self._compact_items_for_log(refreshed), ensure_ascii=False),
        )
        if include_details:
            return {
                "list": refreshed,
                "added_items": added_items_result,
            }
        return refreshed
