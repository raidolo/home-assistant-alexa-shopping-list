#!/usr/bin/env python3

import json
import logging
import os
import threading

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

BROWSER_TIMEOUT_MS = 60000
BROWSER_ACCEPT_LANGUAGE = "en-US,en;q=0.9,it;q=0.8"
BROWSER_GETLIST_PATH = "/alexashoppinglists/api/getlistitems"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class NotAuthenticatedError(Exception):
    """Raised when the Amazon session has expired and login is required."""


class BrowserBackend:
    def __init__(self, amazon_url: str = "amazon.co.uk", cookies_path: str = ""):
        self.amazon_url = amazon_url
        self.cookies_path = cookies_path
        self.is_authenticated = False

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = threading.RLock()

    # ============================================================
    # Lifecycle

    def close(self):
        with self._lock:
            if self._page is not None:
                try:
                    self._page.close()
                except Exception:
                    pass
                self._page = None

            if self._context is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None

            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None

            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _ensure_browser(self):
        if self._page is not None:
            return self._page

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        self._context = self._browser.new_context(
            ignore_https_errors=False,
            locale="it-IT",
            user_agent=BROWSER_USER_AGENT,
            extra_http_headers={
                "Accept-Language": BROWSER_ACCEPT_LANGUAGE,
                "Cache-Control": "max-age=0",
                "Pragma": "no-cache",
            },
            viewport={"width": 1280, "height": 900},
        )

        cookies = self._normalized_playwright_cookies()
        if cookies:
            self._context.add_cookies(cookies)

        self._page = self._context.new_page()
        self._page.set_default_timeout(BROWSER_TIMEOUT_MS)
        self._page.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)
        self._page.goto(self._shopping_list_page_url(), wait_until="domcontentloaded")
        return self._page

    # ============================================================
    # Helpers

    def _get_file_location(self):
        return os.path.dirname(os.path.realpath(__file__))

    def _cookie_cache_path(self):
        if self.cookies_path:
            return os.path.join(self.cookies_path, "cookies.json")
        return os.path.join(self._get_file_location(), "cookies.json")

    def _shopping_list_page_url(self):
        return f"https://www.{self.amazon_url}/alexaquantum/sp/alexaShoppingList?ref=nav_asl"

    def _api_url(self, path: str):
        return f"https://www.{self.amazon_url}{path}"

    def _is_getlistitems_response(self, response):
        return BROWSER_GETLIST_PATH in (response.url or "")

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

    def _normalized_playwright_cookies(self):
        normalized = []
        for cookie in self._read_cookie_cache():
            if not isinstance(cookie, dict):
                continue

            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue

            path = cookie.get("path") or "/"
            domain = cookie.get("domain") or f".{self.amazon_url}"
            normalized_cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
            }

            if isinstance(cookie.get("secure"), bool):
                normalized_cookie["secure"] = cookie["secure"]
            if isinstance(cookie.get("httpOnly"), bool):
                normalized_cookie["httpOnly"] = cookie["httpOnly"]

            expiry = cookie.get("expiry")
            if isinstance(expiry, (int, float)):
                normalized_cookie["expires"] = int(expiry)

            same_site = cookie.get("sameSite")
            if same_site in ("Strict", "Lax", "None"):
                normalized_cookie["sameSite"] = same_site

            normalized.append(normalized_cookie)

        return normalized

    def _response_debug_summary(self, response):
        body = response.get("body") or ""
        compact_body = " ".join(body.split())[:300]
        return {
            "ok": response.get("ok"),
            "status": response.get("status"),
            "content_type": response.get("content_type"),
            "url": response.get("url"),
            "json_type": type(response.get("json")).__name__ if response.get("json") is not None else None,
            "body_snippet": compact_body,
        }

    def _browser_request_json(self, path: str, method: str = "GET", payload=None):
        with self._lock:
            page = self._ensure_browser()

            try:
                response = page.evaluate(
                    """
                    async ({url, method, payload}) => {
                        const headers = {
                            "accept": "application/json",
                            "cache-control": "max-age=0",
                            "pragma": "no-cache"
                        };
                        if (payload !== null) {
                            headers["content-type"] = "application/json";
                        }

                        const res = await fetch(url, {
                            method,
                            credentials: "include",
                            headers,
                            body: payload === null ? undefined : JSON.stringify(payload),
                        });

                        const text = await res.text();
                        return {
                            ok: res.ok,
                            status: res.status,
                            url: res.url,
                            contentType: res.headers.get("content-type"),
                            body: text,
                        };
                    }
                    """,
                    {
                        "url": self._api_url(path),
                        "method": method,
                        "payload": payload,
                    },
                )
            except (PlaywrightTimeoutError, PlaywrightError) as error:
                raise RuntimeError(f"Playwright browser request failed: {error}") from error

            parsed = None
            body = response.get("body") or ""
            if body:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = None

            return {
                "ok": bool(response.get("ok")),
                "status": response.get("status"),
                "url": response.get("url"),
                "content_type": response.get("contentType"),
                "body": body,
                "json": parsed,
            }

    def _capture_page_list_items_response(self):
        with self._lock:
            page = self._ensure_browser()

            try:
                with page.expect_response(self._is_getlistitems_response, timeout=BROWSER_TIMEOUT_MS) as response_info:
                    page.goto(self._shopping_list_page_url(), wait_until="domcontentloaded")
                response = response_info.value
                body = response.text()
            except (PlaywrightTimeoutError, PlaywrightError) as error:
                raise RuntimeError(f"Playwright page getlistitems capture failed: {error}") from error

            parsed = None
            if body:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = None

            return {
                "ok": response.ok,
                "status": response.status,
                "url": response.url,
                "content_type": response.headers.get("content-type"),
                "body": body,
                "json": parsed,
            }

    def _refresh_page_and_get_list_items_payload(self):
        response = self._capture_page_list_items_response()
        self._ensure_authenticated_response(response, "getlistitems")
        if not isinstance(response.get("json"), dict):
            raise RuntimeError("Invalid getlistitems browser response")
        return response["json"]

    def _normalized_current_list(self):
        return self._normalize_list_payload(self._refresh_page_and_get_list_items_payload())

    def _write_and_capture_list(self, action_name: str, path: str, method: str, payload=None):
        response = self._browser_request_json(path, method=method, payload=payload)
        self._ensure_authenticated_response(response, action_name)
        try:
            refreshed = self._normalized_current_list()
        except Exception as error:
            logger.warning(
                "Alexa %s write was accepted but page refresh confirmation failed: %s",
                action_name,
                error,
            )
            refreshed = None
        return response, refreshed

    def _ensure_authenticated_response(self, response, action: str):
        status = response.get("status")
        body = response.get("body") or ""
        response_url = response.get("url") or ""

        if (
            status == 401
            or "AuthenticationFailure" in body
            or "/ap/signin" in response_url
        ):
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

    def _normalize_list_payload(self, payload):
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

    def _items_log_summary(self, items, preview_limit: int = 12):
        compact_items = self._compact_items_for_log(items)
        active_items = [item for item in compact_items if not item.get("complete")]
        completed_items = [item for item in compact_items if item.get("complete")]
        return {
            "active_count": len(active_items),
            "completed_count": len(completed_items),
            "active_preview": active_items[:preview_limit],
            "completed_preview": completed_items[:preview_limit],
        }

    def _get_list_items_payload(self):
        return self._refresh_page_and_get_list_items_payload()

    def _default_list_payload(self):
        payload = self._get_list_items_payload()
        for list_payload in payload.values():
            list_info = list_payload.get("listInfo", {})
            if list_info.get("defaultList"):
                return list_payload
        raise RuntimeError("Default Alexa shopping list not found in getlistitems response")

    def _default_list_id(self):
        list_payload = self._default_list_payload()
        list_info = list_payload.get("listInfo", {})
        list_id = list_info.get("listId")
        if not list_id:
            raise RuntimeError("Default Alexa shopping listId missing")
        return list_id

    def _find_item_by_id(self, list_payload, item_id: str):
        for item in list_payload.get("listItems", []):
            if item.get("id") == item_id:
                return item
        return None

    def _find_list_item(self, list_payload, value: str, completed=None, prefer_latest=False):
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

    def _add_list_item(self, item: str):
        list_id = self._default_list_id()
        response, refreshed = self._write_and_capture_list(
            "addlistitem",
            f"/alexashoppinglists/api/addlistitem/{list_id}",
            method="POST",
            payload={
                "value": item,
                "listItemMetadata": [],
            },
        )
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
            }, refreshed
        return None, refreshed

    def _update_list_item(self, old: str, new: str, alexa_id: str = None):
        list_payload = self._default_list_payload()
        if alexa_id:
            current_item = self._find_item_by_id(list_payload, alexa_id)
            if current_item is not None and bool(current_item.get("completed", False)):
                current_item = None
        else:
            current_item = self._find_list_item(list_payload, old, completed=False, prefer_latest=False)

        if current_item is None:
            return False

        update_payload = dict(current_item)
        update_payload["value"] = new
        _, refreshed = self._write_and_capture_list(
            "updatelistitem",
            "/alexashoppinglists/api/updatelistitem",
            method="PUT",
            payload=update_payload,
        )
        return True, refreshed

    def _complete_list_item(self, item: str, alexa_id: str = None):
        list_payload = self._default_list_payload()
        if alexa_id:
            current_item = self._find_item_by_id(list_payload, alexa_id)
            if current_item is not None and bool(current_item.get("completed", False)):
                current_item = None
        else:
            current_item = self._find_list_item(list_payload, item, completed=False, prefer_latest=False)

        if current_item is None:
            return False

        update_payload = dict(current_item)
        update_payload["completed"] = True
        _, refreshed = self._write_and_capture_list(
            "complete list item",
            "/alexashoppinglists/api/updatelistitem",
            method="PUT",
            payload=update_payload,
        )
        return True, refreshed

    def _remove_list_item(self, item: str):
        list_payload = self._default_list_payload()
        current_item = self._find_list_item(list_payload, item, completed=False, prefer_latest=False)
        if current_item is None:
            return False, self.get_alexa_list()

        _, refreshed = self._write_and_capture_list(
            "deletelistitem",
            "/alexashoppinglists/api/deletelistitem",
            method="DELETE",
            payload=current_item,
        )
        return True, refreshed

    # ============================================================
    # Public API

    def requires_login(self):
        response = self._capture_page_list_items_response()

        if response.get("ok") and isinstance(response.get("json"), dict):
            self.is_authenticated = True
            return False

        status = response.get("status")
        body = response.get("body") or ""
        response_url = response.get("url") or ""
        if (
            status == 401
            or "AuthenticationFailure" in body
            or "/ap/signin" in response_url
        ):
            self.is_authenticated = False
            logger.warning(
                "Amazon auth check determined login is required: %s | cookies=%s",
                json.dumps(self._response_debug_summary(response), ensure_ascii=False),
                json.dumps(self._cookie_debug_summary(), ensure_ascii=False),
            )
            return True

        logger.warning(
            "Amazon browser auth check returned an unexpected response: %s | cookies=%s",
            json.dumps(self._response_debug_summary(response), ensure_ascii=False),
            json.dumps(self._cookie_debug_summary(), ensure_ascii=False),
        )
        raise RuntimeError(
            f"Unexpected browser auth check response: status={status}, body={body[:200]}"
        )

    def get_alexa_list(self):
        alexa_items = self._normalize_list_payload(self._get_list_items_payload())
        logger.info("Alexa list read via Playwright browser API")
        return alexa_items

    def complete_alexa_list_item(self, item: str, alexa_id: str = None):
        completed, refreshed = self._complete_list_item(item, alexa_id=alexa_id)
        if not completed:
            return refreshed
        if refreshed is None:
            refreshed = self.get_alexa_list()
        logger.info(
            "Alexa browser complete result for '%s': %s",
            item,
            json.dumps(self._items_log_summary(refreshed), ensure_ascii=False),
        )
        return refreshed

    def add_alexa_list_item(self, item: str, include_details: bool = False):
        logger.info("Alexa browser add requested: %s", item)
        added_item, refreshed = self._add_list_item(item)
        if refreshed is None:
            refreshed = self.get_alexa_list()
        logger.info(
            "Alexa browser add result for '%s': %s",
            item,
            json.dumps(self._items_log_summary(refreshed), ensure_ascii=False),
        )
        if include_details:
            return {
                "list": refreshed,
                "added_items": [added_item] if added_item is not None else [],
            }
        return refreshed

    def update_alexa_list_item(self, old: str, new: str, alexa_id: str = None):
        _, refreshed = self._update_list_item(old, new, alexa_id=alexa_id)
        if refreshed is None:
            refreshed = self.get_alexa_list()
        logger.info(
            "Alexa browser update result for '%s' -> '%s': %s",
            old,
            new,
            json.dumps(self._items_log_summary(refreshed), ensure_ascii=False),
        )
        return refreshed

    def remove_alexa_list_item(self, item: str):
        _, refreshed = self._remove_list_item(item)
        if refreshed is None:
            refreshed = self.get_alexa_list()
        logger.info(
            "Alexa browser delete result for '%s': %s",
            item,
            json.dumps(self._items_log_summary(refreshed), ensure_ascii=False),
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
            "Alexa browser bulk apply requested: %s",
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
            logger.info("Alexa browser bulk add item: %s", item)
            added_item, _ = self._add_list_item(item)
            if added_item is not None:
                added_items_result.append(added_item)

        for item in remove_items:
            logger.info("Alexa browser bulk remove item: %s", item)
            self._remove_list_item(item)

        for update in update_items:
            logger.info("Alexa browser bulk update item: %s", json.dumps(update, ensure_ascii=False))
            self._update_list_item(
                update["old"],
                update["new"],
                alexa_id=update.get("alexa_id"),
            )

        for item in complete_items:
            logger.info("Alexa browser bulk complete item: %s", item)
            if isinstance(item, dict):
                self._complete_list_item(
                    item.get("name") or "",
                    alexa_id=item.get("alexa_id"),
                )
            else:
                self._complete_list_item(item)

        try:
            refreshed = self._normalized_current_list()
        except Exception as error:
            logger.warning(
                "Alexa bulk apply completed but page refresh confirmation failed: %s",
                error,
            )
            refreshed = self.get_alexa_list()
        logger.info(
            "Alexa browser bulk apply result: %s",
            json.dumps(self._items_log_summary(refreshed), ensure_ascii=False),
        )
        if include_details:
            return {
                "list": refreshed,
                "added_items": added_items_result,
            }
        return refreshed
