#!/usr/bin/env python3

import json
import logging
import os
import queue
import threading
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

BROWSER_TIMEOUT_MS = 60000
BROWSER_AUTH_TIMEOUT_MS = 10000
BROWSER_IDLE_CLOSE_SECONDS = 120
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
        self._last_list_response = None
        self._idle_close_timer = None
        self._worker_queue = queue.Queue()
        self._worker_thread = None
        self._worker_thread_id = None
        self._worker_stop = object()

    # ============================================================
    # Lifecycle

    def _ensure_worker(self):
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return

            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="asl-playwright-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def _worker_loop(self):
        self._worker_thread_id = threading.get_ident()
        while True:
            task = self._worker_queue.get()
            if task is self._worker_stop:
                break

            func, result_queue = task
            try:
                result_queue.put((True, func()))
            except Exception as error:
                result_queue.put((False, error))

        self._worker_thread_id = None

    def _run_on_worker(self, func):
        if threading.get_ident() == self._worker_thread_id:
            return func()

        self._ensure_worker()
        result_queue = queue.Queue(maxsize=1)
        self._worker_queue.put((func, result_queue))
        ok, value = result_queue.get()
        if ok:
            return value
        raise value

    def _dispose_browser_internal(self):
        self._cancel_idle_close_timer()
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

    def close(self):
        with self._lock:
            self._cancel_idle_close_timer()
            worker = self._worker_thread

        if worker is None:
            self._dispose_browser_internal()
            return

        try:
            self._run_on_worker(self._dispose_browser_internal)
        finally:
            if threading.get_ident() != self._worker_thread_id:
                self._worker_queue.put(self._worker_stop)
                worker.join(timeout=5)
            with self._lock:
                self._worker_thread = None
                self._worker_thread_id = None

    def _cancel_idle_close_timer(self):
        if self._idle_close_timer is not None:
            self._idle_close_timer.cancel()
            self._idle_close_timer = None

    def _schedule_idle_close(self):
        with self._lock:
            self._cancel_idle_close_timer()
            if self._page is None:
                return

            self._idle_close_timer = threading.Timer(
                BROWSER_IDLE_CLOSE_SECONDS,
                self._close_for_idle_timeout,
            )
            self._idle_close_timer.daemon = True
            self._idle_close_timer.start()

    def _close_for_idle_timeout(self):
        with self._lock:
            has_page = self._page is not None
            self._idle_close_timer = None

        if not has_page:
            return

        logger.info(
            "Playwright browser idle timeout reached (%ss), closing browser",
            BROWSER_IDLE_CLOSE_SECONDS,
        )
        try:
            self._run_on_worker(self._dispose_browser_internal)
        except Exception as error:
            logger.warning("Playwright idle close failed: %s", error)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _ensure_browser(self):
        if self._page is not None:
            self._cancel_idle_close_timer()
            return self._page

        self._cancel_idle_close_timer()
        self._playwright = sync_playwright().start()
        browser_type = self._playwright.chromium
        logger.info(
            "Launching Playwright Chromium browser (executable_path=%s, headless=%s)",
            getattr(browser_type, "executable_path", "<unknown>"),
            True,
        )
        self._browser = browser_type.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        logger.info(
            "Playwright Chromium launched (version=%s)",
            self._browser.version,
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
        self._page.on("request", self._log_page_request)
        self._page.on("response", self._log_page_response)
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

    def _shopping_list_page_url_with_nonce(self):
        return f"{self._shopping_list_page_url()}&_aslts={int(time.time() * 1000)}"

    def _api_url(self, path: str):
        return f"https://www.{self.amazon_url}{path}"

    def _is_getlistitems_response(self, response):
        return BROWSER_GETLIST_PATH in (response.url or "")

    def _should_log_network_event(self, url: str, resource_type: str = ""):
        if not url:
            return False
        if "alexashoppinglists" in url:
            return True
        if resource_type == "fetch" and "amazon." in url:
            return True
        return False

    def _log_page_request(self, request):
        try:
            url = request.url or ""
            resource_type = request.resource_type or ""
            if not self._should_log_network_event(url, resource_type):
                return
            logger.info(
                "Playwright request observed (resource_type=%s, method=%s, url=%s)",
                resource_type,
                request.method,
                url,
            )
        except Exception:
            return

    def _log_page_response(self, response):
        try:
            url = response.url or ""
            request = response.request
            resource_type = request.resource_type or ""
            if not self._should_log_network_event(url, resource_type):
                return
            logger.info(
                "Playwright response observed (resource_type=%s, method=%s, status=%s, url=%s)",
                resource_type,
                request.method,
                response.status,
                url,
            )
        except Exception:
            return

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

    def _response_requires_login(self, response):
        status = response.get("status")
        body = (response.get("body") or "").lower()
        response_url = (response.get("url") or "").lower()

        login_markers = (
            "/ap/signin",
            "/ap/mfa",
            "authenticationfailure",
            "sign in",
            "ap/signin",
            "ap/mfa",
        )

        if status == 401:
            return True

        if any(marker in response_url for marker in ("/ap/signin", "/ap/mfa")):
            return True

        if any(marker in body for marker in login_markers):
            return True

        return False

    def _mark_not_authenticated(self, action: str, response):
        self.is_authenticated = False
        logger.warning(
            "Amazon auth rejected during %s: %s | cookies=%s",
            action,
            json.dumps(self._response_debug_summary(response), ensure_ascii=False),
            json.dumps(self._cookie_debug_summary(), ensure_ascii=False),
        )

    def _browser_request_json(self, path: str, method: str = "GET", payload=None):
        try:
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
        finally:
            self._schedule_idle_close()

    def _open_json_in_browser(self, path: str, action_name: str):
        try:
            with self._lock:
                self._ensure_browser()
                page = self._context.new_page()
                page.set_default_timeout(BROWSER_TIMEOUT_MS)
                page.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)

                try:
                    api_url = self._api_url(path)
                    logger.info("Opening %s directly in browser page: %s", action_name, api_url)
                    response = page.goto(api_url, wait_until="domcontentloaded")
                    if response is None:
                        raise RuntimeError(f"Browser navigation for {action_name} returned no response")
                    body = page.locator("body").inner_text(timeout=5000)
                except (PlaywrightTimeoutError, PlaywrightError) as error:
                    current_url = page.url or ""
                    if "/ap/signin" in current_url or "/ap/mfa" in current_url:
                        return {
                            "ok": False,
                            "status": 401,
                            "url": current_url,
                            "content_type": "text/html",
                            "body": "",
                            "json": None,
                        }
                    raise RuntimeError(f"Playwright browser page open failed during {action_name}: {error}") from error
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

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
        finally:
            self._schedule_idle_close()

    def _navigate_for_auth_check(self):
        return self._open_json_in_browser(BROWSER_GETLIST_PATH, "auth check")

    def _refresh_page_and_get_list_items_payload(self):
        response = self._open_json_in_browser(BROWSER_GETLIST_PATH, "getlistitems")
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
        if self._response_requires_login(response):
            self._mark_not_authenticated(action, response)
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
        return self._run_on_worker(self._requires_login_impl)

    def _requires_login_impl(self):
        response = self._navigate_for_auth_check()

        if self._response_requires_login(response):
            self._mark_not_authenticated("auth check", response)
            return True

        if response.get("ok") and isinstance(response.get("json"), dict):
            self.is_authenticated = True
            return False

        status = response.get("status")
        body = response.get("body") or ""
        logger.warning(
            "Amazon browser auth check returned a non-auth unexpected response: %s | cookies=%s",
            json.dumps(self._response_debug_summary(response), ensure_ascii=False),
            json.dumps(self._cookie_debug_summary(), ensure_ascii=False),
        )
        raise RuntimeError(
            f"Unexpected browser auth check response: status={status}, body={body[:200]}"
        )

    def get_alexa_list(self):
        return self._run_on_worker(self._get_alexa_list_impl)

    def _get_alexa_list_impl(self):
        alexa_items = self._normalize_list_payload(self._get_list_items_payload())
        logger.info("Alexa list read via Playwright browser API")
        return alexa_items

    def complete_alexa_list_item(self, item: str, alexa_id: str = None):
        return self._run_on_worker(lambda: self._complete_alexa_list_item_impl(item, alexa_id=alexa_id))

    def _complete_alexa_list_item_impl(self, item: str, alexa_id: str = None):
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
        return self._run_on_worker(
            lambda: self._add_alexa_list_item_impl(item, include_details=include_details)
        )

    def _add_alexa_list_item_impl(self, item: str, include_details: bool = False):
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
        return self._run_on_worker(
            lambda: self._update_alexa_list_item_impl(old, new, alexa_id=alexa_id)
        )

    def _update_alexa_list_item_impl(self, old: str, new: str, alexa_id: str = None):
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
        return self._run_on_worker(lambda: self._remove_alexa_list_item_impl(item))

    def _remove_alexa_list_item_impl(self, item: str):
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
        return self._run_on_worker(
            lambda: self._bulk_apply_alexa_list_changes_impl(
                add_items=add_items,
                remove_items=remove_items,
                update_items=update_items,
                complete_items=complete_items,
                include_details=include_details,
            )
        )

    def _bulk_apply_alexa_list_changes_impl(
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
