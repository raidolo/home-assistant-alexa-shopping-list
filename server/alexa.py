#!/usr/bin/env python3

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
import time
import json
import os
import logging
import datetime
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

WAIT_TIMEOUT=30

class NotAuthenticatedError(Exception):
    """Raised when the Amazon session has expired and login is required."""
    pass


class InvalidListStateError(Exception):
    """Raised when the Alexa shopping list page is not in a trustworthy state."""
    pass

class AlexaShoppingList:

    def __init__(self, amazon_url: str = "amazon.co.uk", cookies_path: str = ""):
        self.amazon_url = amazon_url
        self.cookies_path = cookies_path
        self._setup_driver()


    def __del__(self):
        self._clear_driver()

    # ============================================================
    # Helpers


    def _get_file_location(self):
        return os.path.dirname(os.path.realpath(__file__))

    def _is_template_literal(self, value: str) -> bool:
        return value.startswith("{{") and value.endswith("}}")

    def _load_addon_options(self):
        if hasattr(self, "_addon_options"):
            return

        self._addon_options = {}
        options_path = os.environ.get("ALEXA_SHOPPING_LIST_ADDON_OPTIONS_PATH", "/data/options.json")

        try:
            with open(options_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._addon_options = loaded
        except Exception:
            # Running outside Home Assistant add-on context is expected.
            pass

    def _get_addon_option(self, key: str, default=None):
        self._load_addon_options()
        return self._addon_options.get(key, default)

    def _is_debug_mode(self):
        configured = os.environ.get("ALEXA_SHOPPING_LIST_DEBUG", "").strip()

        if configured and not self._is_template_literal(configured):
            return configured.lower() in ("1", "true", "yes", "on")

        option_debug = self._get_addon_option("ALEXA_SHOPPING_LIST_DEBUG", False)
        if isinstance(option_debug, bool):
            return option_debug
        if isinstance(option_debug, str):
            return option_debug.strip().lower() in ("1", "true", "yes", "on")

        return bool(option_debug)


    def _debug_log_path(self):
        configured = os.environ.get("ALEXA_SHOPPING_LIST_DEBUG_LOG_PATH", "").strip()
        if configured and not self._is_template_literal(configured):
            return configured

        option_path = self._get_addon_option("ALEXA_SHOPPING_LIST_DEBUG_LOG_PATH", "")
        if isinstance(option_path, str) and option_path.strip() != "":
            return option_path.strip()

        base = self.cookies_path or self._get_file_location()
        return os.path.join(base, "chromium_debug.log")

    # ============================================================
    # Selenium


    def _setup_driver(self):
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

        chrome_options = Options()
        # Keep headless mode always enabled inside containerized environments.
        # Debug mode only controls verbosity/log output, not headed execution.
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("window-size=1366,768")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument(f"--user-agent={user_agent}")

        if self._is_debug_mode():
            debug_log_path = self._debug_log_path()
            chrome_options.add_argument("--enable-logging")
            chrome_options.add_argument("--v=1")
            chrome_options.add_argument("--verbose")
            chrome_options.add_argument(f"--log-file={debug_log_path}")
            logger.info(f"Debug mode enabled, Chromium log path: {debug_log_path}")

        driver_path = os.environ.get("CHROME_DRIVER", "")
        if driver_path != "":
            service_kwargs = {
                "executable_path": driver_path
            }
            if self._is_debug_mode():
                debug_log_path = self._debug_log_path()
                service_kwargs["service_args"] = ["--verbose", f"--log-path={debug_log_path}"]
            service = webdriver.ChromeService(
                **service_kwargs
            )
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            self.driver = webdriver.Chrome(options=chrome_options)

        self.is_authenticated = False
        self._selenium_get("https://www."+self.amazon_url, (By.TAG_NAME, 'body'))
        self._load_cookies()

        if len(self.driver.find_elements(By.ID, 'nav-backup-backup')) > 0:
            # I don't know why this is, but random amazon displays some weird page instead of the usual home page.
            # This solution only works for versions of amazon in english, so would cause problems for other languages.
            # But this only happens rarely, so... whatever.
            self.driver.find_element(By.CLASS_NAME, "nav-bb-right").find_element(By.LINK_TEXT, "Your Account").click()
            time.sleep(5)

        if len(self.driver.find_elements(By.CLASS_NAME, 'nav-action-signin-button')) > 0:
            self.driver.find_element(By.ID, 'nav-link-accountList').click()
            time.sleep(5)
        else:
            self.is_authenticated = True



    def _clear_driver(self):
        if hasattr(self, "driver"):
            self.save_session()
            self.driver.quit()


    def _selenium_wait_element(self, element: tuple):
        try:
            WebDriverWait(self.driver, WAIT_TIMEOUT).until(EC.presence_of_element_located(element))
        except TimeoutException:
            current_url = self.driver.current_url
            logger.error(f"Timeout waiting for element {element}. Current URL: {current_url}")
            try:
                screenshot_path = os.path.join(self.cookies_path or self._get_file_location(), "debug_timeout.png")
                self.driver.save_screenshot(screenshot_path)
                logger.error(f"Screenshot saved to {screenshot_path}")
            except Exception as screenshot_err:
                logger.error(f"Failed to save screenshot: {screenshot_err}")
            page_source = self.driver.page_source[:3000] if self.driver.page_source else "empty"
            logger.error(f"Page source snippet: {page_source}")
            raise


    def _selenium_wait_page_ready(self):
        WebDriverWait(self.driver, WAIT_TIMEOUT).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )


    def _selenium_get(self, url: str, wait_for_element: tuple=None, wait_for_page_load: bool=False):
        self.driver.get(url)

        if wait_for_element != None:
            self._selenium_wait_element(wait_for_element)

        if wait_for_page_load:
            self._selenium_wait_page_ready()


    def _cookie_cache_path(self):
        if self.cookies_path != "":
            return os.path.join(self.cookies_path, "cookies.json")
        return os.path.join(self._get_file_location(), "cookies.json")


    def _load_cookies(self):
        if os.path.exists(self._cookie_cache_path()):

            with open(self._cookie_cache_path(), 'r') as file:
                cookies = json.load(file)

            for cookie in cookies:
                self.driver.add_cookie(cookie)

            self.driver.get(self.driver.current_url)
            self._selenium_wait_element((By.ID, 'nav-link-accountList'))


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


    def _debug_dump_getlistitems_api_http(self):
        cookie_header = "; ".join(
            f"{cookie.get('name')}={cookie.get('value')}"
            for cookie in self._read_cookie_cache()
            if cookie.get("name") and cookie.get("value") is not None
        )

        if cookie_header == "":
            raise RuntimeError("No cookies available for HTTP API dump")

        api_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/getlistitems"
        request = urllib.request.Request(
            api_url,
            headers={
                "accept": "application/json",
                "cookie": cookie_header,
                "referer": "https://www." + self.amazon_url + "/alexaquantum/sp/alexaShoppingList?ref=nav_asl",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            },
            method="GET",
        )

        with urllib.request.urlopen(request, timeout=WAIT_TIMEOUT) as response:
            body = response.read().decode("utf-8")
            result = {
                "ok": True,
                "status": response.status,
                "body": body,
            }

        timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
        dump_path = os.path.join("/tmp", f"dump_http_request_getlistitems_{timestamp}.json")

        with open(dump_path, "w", encoding="utf-8") as dump_file:
            json.dump(result, dump_file, indent=2, ensure_ascii=False)

        logger.info(f"Alexa getlistitems HTTP dump saved to {dump_path}")
        return result, dump_path


    def _http_dump_dir(self):
        preferred = "/config"
        if os.path.isdir(preferred) and os.access(preferred, os.W_OK):
            return preferred
        if self.cookies_path and os.path.isdir(self.cookies_path) and os.access(self.cookies_path, os.W_OK):
            return self.cookies_path
        return self._get_file_location()


    def _http_dump_path(self, suffix: str):
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
        filename = f"alexa_http_smoke_{suffix}_{timestamp}.json"
        return os.path.join(self._http_dump_dir(), filename)


    def _http_write_dump(self, suffix: str, payload):
        dump_path = self._http_dump_path(suffix)
        with open(dump_path, "w", encoding="utf-8") as dump_file:
            json.dump(payload, dump_file, indent=2, ensure_ascii=False)
        logger.info(f"Alexa HTTP smoke dump saved to {dump_path}")
        return dump_path


    def _http_cookie_header(self):
        cookie_header = "; ".join(
            f"{cookie.get('name')}={cookie.get('value')}"
            for cookie in self._read_cookie_cache()
            if cookie.get("name") and cookie.get("value") is not None
        )

        if cookie_header == "":
            raise RuntimeError("No cookies available for HTTP request")

        return cookie_header


    def _http_request_json(self, url: str, method: str = "GET", payload=None):
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "cookie": self._http_cookie_header(),
            "referer": "https://www." + self.amazon_url + "/alexaquantum/sp/alexaShoppingList?ref=nav_asl",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
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

        try:
            with urllib.request.urlopen(request, timeout=WAIT_TIMEOUT) as response:
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
            return {
                "ok": False,
                "status": error.code,
                "body": body,
                "json": parsed,
            }


    def _http_get_list_items_json(self):
        api_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/getlistitems"
        response = self._http_request_json(api_url, method="GET")
        if not response.get("ok") or not isinstance(response.get("json"), dict):
            raise RuntimeError("Invalid getlistitems HTTP response")
        return response["json"]


    def _http_requires_login(self):
        api_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/getlistitems"
        response = self._http_request_json(api_url, method="GET")

        if response.get("ok") and isinstance(response.get("json"), dict):
            self.is_authenticated = True
            return False

        status = response.get("status")
        body = response.get("body") or ""
        if status == 401 or "AuthenticationFailure" in body:
            self.is_authenticated = False
            return True

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
                normalized.append({
                    "id": item_id,
                    "name": item_name,
                    "complete": bool(item.get("completed", False)),
                    "createdDateTime": item.get("createdDateTime"),
                    "updatedDateTime": item.get("updatedDateTime"),
                    "version": item.get("version"),
                    "listId": item.get("listId") or list_id,
                    "defaultList": default_list,
                })

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
            compact.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "complete": bool(item.get("complete", False)),
            })
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


    def _http_find_latest_item_by_value(self, list_payload, value: str):
        candidates = [
            item for item in list_payload.get("listItems", [])
            if item.get("value") == value
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                int(item.get("createdDateTime", 0)),
                int(item.get("updatedDateTime", 0)),
            ),
        )


    def _http_add_list_item(self, item: str):
        list_id = self._http_default_list_id()
        add_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/addlistitem/" + list_id
        response = self._http_request_json(
            add_url,
            method="POST",
            payload={
                "value": item,
                "listItemMetadata": [],
            },
        )
        if response.get("ok") and isinstance(response.get("json"), dict):
            return {
                "id": response["json"].get("id"),
                "name": response["json"].get("value"),
                "complete": bool(response["json"].get("completed", False)),
                "createdDateTime": response["json"].get("createdDateTime"),
                "updatedDateTime": response["json"].get("updatedDateTime"),
                "version": response["json"].get("version"),
                "listId": response["json"].get("listId"),
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
        update_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/updatelistitem"
        self._http_request_json(update_url, method="PUT", payload=update_payload)
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
        update_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/updatelistitem"
        self._http_request_json(update_url, method="PUT", payload=update_payload)
        return True


    def _http_remove_list_item(self, item: str):
        list_payload = self._http_default_list_payload()
        current_item = self._http_find_list_item(list_payload, item, completed=False, prefer_latest=False)
        if current_item is None:
            return False

        delete_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/deletelistitem"
        self._http_request_json(delete_url, method="DELETE", payload=current_item)
        return True


    def run_http_api_startup_smoke_test(self):
        logger.info("Starting Alexa HTTP API startup smoke test")

        default_list = self._http_default_list_payload()
        list_id = default_list.get("listInfo", {}).get("listId")
        if not list_id:
            raise RuntimeError("Default Alexa listId missing from getlistitems response")

        add_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/addlistitem/" + list_id
        update_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/updatelistitem"
        delete_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/deletelistitem"

        self._http_request_json(
            add_url,
            method="POST",
            payload={"value": "test", "listItemMetadata": []},
        )
        add_dump = self._http_get_list_items_json()
        self._http_write_dump("_add", add_dump)

        added_default_list = None
        for list_payload in add_dump.values():
            list_info = list_payload.get("listInfo", {})
            if list_info.get("listId") == list_id:
                added_default_list = list_payload
                break
        if added_default_list is None:
            raise RuntimeError("Default list missing from _add dump")

        added_item = self._http_find_latest_item_by_value(added_default_list, "test")
        if added_item is None:
            raise RuntimeError("Unable to find newly added 'test' item")

        renamed_item = dict(added_item)
        renamed_item["value"] = "test rename"
        self._http_request_json(update_url, method="PUT", payload=renamed_item)
        rename_dump = self._http_get_list_items_json()
        self._http_write_dump("_rename", rename_dump)

        renamed_default_list = None
        for list_payload in rename_dump.values():
            list_info = list_payload.get("listInfo", {})
            if list_info.get("listId") == list_id:
                renamed_default_list = list_payload
                break
        if renamed_default_list is None:
            raise RuntimeError("Default list missing from _rename dump")

        renamed_item = self._http_find_item_by_id(renamed_default_list, added_item["id"])
        if renamed_item is None:
            raise RuntimeError("Unable to find renamed item by id")

        completed_item = dict(renamed_item)
        completed_item["completed"] = True
        self._http_request_json(update_url, method="PUT", payload=completed_item)
        complete_dump = self._http_get_list_items_json()
        self._http_write_dump("_complete", complete_dump)

        self._http_request_json(
            add_url,
            method="POST",
            payload={"value": "test delete", "listItemMetadata": []},
        )
        delete_add_dump = self._http_get_list_items_json()
        self._http_write_dump("_test_add_per_delete", delete_add_dump)

        delete_default_list = None
        for list_payload in delete_add_dump.values():
            list_info = list_payload.get("listInfo", {})
            if list_info.get("listId") == list_id:
                delete_default_list = list_payload
                break
        if delete_default_list is None:
            raise RuntimeError("Default list missing from _test_add_per_delete dump")

        delete_item = self._http_find_latest_item_by_value(delete_default_list, "test delete")
        if delete_item is None:
            raise RuntimeError("Unable to find newly added 'test delete' item")

        self._http_request_json(delete_url, method="DELETE", payload=delete_item)
        delete_dump = self._http_get_list_items_json()
        self._http_write_dump("_test_delete", delete_dump)

        logger.info(
            "Alexa HTTP API startup smoke test completed: add=%s rename=%s complete=%s delete=%s",
            added_item.get("id"),
            renamed_item.get("id"),
            completed_item.get("id"),
            delete_item.get("id"),
        )




    # ============================================================
    # Authentication


    def requires_login(self):
        try:
            return self._http_requires_login()
        except Exception as http_error:
            logger.warning(f"HTTP auth check failed, falling back to Selenium auth check: {http_error}")

        try:
            self._ensure_driver_is_on_alexa_list()
        except NotAuthenticatedError:
            self.is_authenticated = False
            return True
        except Exception:
            pass

        if 'ap/signin' in self.driver.current_url:
            return True

        if len(self.driver.find_elements(By.CLASS_NAME, 'nav-action-signin-button')) > 0:
            return True

        if self.is_authenticated == False:
            return True

        return False
    

    def save_session(self):
        if self.is_authenticated:
            with open(self._cookie_cache_path(), 'w') as file:
                json.dump(self.driver.get_cookies(), file)

    # ============================================================
    # Alexa lists


    def _check_auth_redirect(self):
        """Check if Amazon redirected to a login page. Raises NotAuthenticatedError if so."""
        current_url = self.driver.current_url
        auth_indicators = ['ap/signin', 'ap/mfa', 'ap/cvf', 'ap/challenge']
        for indicator in auth_indicators:
            if indicator in current_url:
                logger.warning(f"Session expired: redirected to {current_url}")
                self.is_authenticated = False
                raise NotAuthenticatedError(f"Amazon session expired (redirected to login: {indicator})")


    def _ensure_driver_is_on_alexa_list(self, refresh: bool = False):
        list_url = "https://www."+self.amazon_url+"/alexaquantum/sp/alexaShoppingList"
        if "/alexaquantum/sp/alexaShoppingList" not in self.driver.current_url:
            self.driver.get(list_url)
            self._selenium_wait_page_ready()
            self._check_auth_redirect()
            self._selenium_wait_element((By.CLASS_NAME, 'virtual-list'))
        elif refresh == True:
            self.driver.get(self.driver.current_url)
            self._selenium_wait_page_ready()
            self._check_auth_redirect()
            self._selenium_wait_element((By.CLASS_NAME, 'virtual-list'))


    def _wait_for_alexa_list_ready(self, timeout: int = 10):
        def _list_ready(driver):
            self._check_auth_redirect()

            if "/alexaquantum/sp/alexaShoppingList" not in driver.current_url:
                return False

            containers = driver.find_elements(By.CLASS_NAME, 'virtual-list')
            headers = driver.find_elements(By.CLASS_NAME, 'list-header')
            return len(containers) > 0 and len(headers) > 0

        WebDriverWait(self.driver, timeout).until(_list_ready)


    def _prepare_alexa_list_page(self, refresh: bool = False):
        self._ensure_driver_is_on_alexa_list(refresh)
        self._wait_for_alexa_list_ready()


    def _wait_for_alexa_list_items(self, timeout: int = 5):
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: len(driver.find_elements(By.CLASS_NAME, 'item-title')) > 0
            )
            return True
        except TimeoutException:
            return False


    def _debug_dump_getlistitems_api(self):
        api_url = "https://www." + self.amazon_url + "/alexashoppinglists/api/getlistitems"
        script = """
const url = arguments[0];
const done = arguments[arguments.length - 1];

fetch(url, {
  method: 'GET',
  credentials: 'include',
  headers: {
    'accept': 'application/json'
  }
})
  .then(async (response) => {
    const body = await response.text();
    done({
      ok: response.ok,
      status: response.status,
      body
    });
  })
  .catch((error) => {
    done({
      ok: false,
      error: String(error)
    });
  });
"""
        result = self.driver.execute_async_script(script, api_url)
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
        dump_path = os.path.join("/tmp", f"alexa_getlistitems_{timestamp}.json")

        with open(dump_path, "w", encoding="utf-8") as dump_file:
            json.dump(result, dump_file, indent=2, ensure_ascii=False)

        logger.info(f"Alexa getlistitems API dump saved to {dump_path}")
        return result, dump_path


    def _get_alexa_list_container(self):
        self._check_auth_redirect()

        current_url = self.driver.current_url
        if "/alexaquantum/sp/alexaShoppingList" not in current_url:
            raise InvalidListStateError(f"Unexpected Alexa shopping list URL: {current_url}")

        containers = self.driver.find_elements(By.CLASS_NAME, 'virtual-list')
        if len(containers) == 0:
            raise InvalidListStateError("Alexa shopping list container not found")

        headers = self.driver.find_elements(By.CLASS_NAME, 'list-header')
        if len(headers) == 0:
            raise InvalidListStateError("Alexa shopping list header not found")

        return containers[0]


    def _extract_alexa_list_items(self, list_container):
        found = []
        previous_visible = []
        previous_signature = None
        max_scrolls = 50
        scroll_count = 0

        while True:
            try:
                list_items = list_container.find_elements(By.CLASS_NAME, 'item-title')
                current_visible = []
                for item in list_items:
                    text = item.get_attribute('innerText')
                    if text:
                        current_visible.append(text)

                if not current_visible:
                    break

                current_signature = json.dumps(current_visible, ensure_ascii=False)
                if not found:
                    found.extend(current_visible)
                else:
                    previous_index = 0
                    new_items = []
                    for item_text in current_visible:
                        if previous_index < len(previous_visible) and item_text == previous_visible[previous_index]:
                            previous_index += 1
                        else:
                            new_items.append(item_text)
                    found.extend(new_items)

                if current_signature == previous_signature:
                    break

                previous_visible = current_visible
                previous_signature = current_signature
                scroll_count += 1
                if scroll_count >= max_scrolls:
                    break

                self.driver.execute_script("arguments[0].scrollIntoView();", list_items[-1])
                time.sleep(1)
            except StaleElementReferenceException:
                time.sleep(1)
                continue

        return found


    def _wait_for_element_staleness(self, element, timeout: int = 5):
        try:
            WebDriverWait(self.driver, timeout).until(EC.staleness_of(element))
            return True
        except TimeoutException:
            logger.debug("Timed out waiting for DOM update after list mutation")
            return False


    def _validate_empty_alexa_list_result(self, list_container):
        self._check_auth_redirect()

        current_url = self.driver.current_url
        if "/alexaquantum/sp/alexaShoppingList" not in current_url:
            raise InvalidListStateError(f"Unexpected Alexa shopping list URL after scrape: {current_url}")

        if list_container.get_attribute("class") is None:
            raise InvalidListStateError("Alexa shopping list container became stale")

        headers = self.driver.find_elements(By.CLASS_NAME, 'list-header')
        if len(headers) == 0:
            raise InvalidListStateError("Alexa shopping list header missing after empty scrape")


    def get_alexa_list(self, refresh: bool = True):
        try:
            alexa_items = self._get_alexa_list_http()
            logger.info("Alexa list read via HTTP API")
            return alexa_items
        except Exception as http_error:
            logger.warning(f"Alexa HTTP list read failed, falling back to DOM scrape: {http_error}")

        self._prepare_alexa_list_page(refresh)
        try:
            http_api_result, http_dump_path = self._debug_dump_getlistitems_api_http()
            logger.info(
                "Alexa getlistitems HTTP result: %s",
                json.dumps(
                    {
                        "ok": http_api_result.get("ok"),
                        "status": http_api_result.get("status"),
                        "dump_path": http_dump_path,
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception as e:
            logger.info(f"Alexa getlistitems HTTP dump failed: {e}")
        try:
            api_result, dump_path = self._debug_dump_getlistitems_api()
            logger.info(
                "Alexa getlistitems API result: %s",
                json.dumps(
                    {
                        "ok": api_result.get("ok"),
                        "status": api_result.get("status"),
                        "dump_path": dump_path,
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception as e:
            logger.info(f"Alexa getlistitems API dump failed: {e}")
        self._wait_for_alexa_list_items()
        list_container = self._get_alexa_list_container()
        found = self._extract_alexa_list_items(list_container)

        if len(found) == 0:
            # The page shell can render before list items are hydrated.
            # Retry the extraction once more before accepting a true empty list.
            self._wait_for_alexa_list_items()
            list_container = self._get_alexa_list_container()
            found = self._extract_alexa_list_items(list_container)
            self._validate_empty_alexa_list_result(list_container)

        if not refresh:
            # Now let's scroll back to the top
            first_text = None
            while True:
                try:
                    list_items = list_container.find_elements(By.CLASS_NAME, 'item-title')
                    current_first_text = list_items[0].get_attribute('innerText') if list_items else None
                    if not list_items or current_first_text == first_text:
                        # We've reached the top
                        break
                    first_text = current_first_text
                    scroll_origin = ScrollOrigin.from_element(list_items[0])
                    ActionChains(self.driver).scroll_from_origin(scroll_origin, 0, -1000).perform()
                except StaleElementReferenceException:
                    time.sleep(1)
                    continue

        return [
            {
                "id": None,
                "name": item_name,
                "complete": False,
                "createdDateTime": None,
                "updatedDateTime": None,
                "version": None,
                "listId": None,
                "defaultList": True,
            }
            for item_name in found
        ]


    def _get_alexa_list_item_element(self, item: str, ensure_page_ready: bool = True):
        if ensure_page_ready:
            self._prepare_alexa_list_page(False)
        self._wait_for_alexa_list_items()
        list_container = self.driver.find_element(By.CLASS_NAME, 'virtual-list')

        last_text = None
        max_scrolls = 50
        scroll_count = 0
        while True:
            try:
                list_items = list_container.find_elements(By.CLASS_NAME, 'inner')
                for container in list_items:
                    title_element = container.find_element(By.CLASS_NAME, 'item-title')
                    if title_element.get_attribute('innerText') == item:
                        return container  # Return immediately when found

                current_last_text = None
                if list_items:
                    last_title = list_items[-1].find_element(By.CLASS_NAME, 'item-title')
                    current_last_text = last_title.get_attribute('innerText')

                if not list_items or current_last_text == last_text:
                    # We've reached the end
                    break

                last_text = current_last_text
                scroll_count += 1
                if scroll_count >= max_scrolls:
                    break
                self.driver.execute_script("arguments[0].scrollIntoView();", list_items[-1])
                time.sleep(1)
            except StaleElementReferenceException:
                time.sleep(1)
                continue

        return None


    def _find_completion_toggle(self, element):
        checkbox = element.find_element(By.CSS_SELECTOR, ".checkBox input[type='checkbox']")
        checkbox_id = checkbox.get_attribute('id')

        if checkbox_id:
            labels = element.find_elements(By.CSS_SELECTOR, f"label[for='{checkbox_id}']")
            if len(labels) > 0:
                return labels[0]

        return checkbox


    def complete_alexa_list_item(self, item: str, alexa_id: str = None):
        return self._complete_alexa_list_item(item, refresh_result=True, alexa_id=alexa_id)


    def _complete_alexa_list_item(self, item: str, refresh_result: bool = True, ensure_page_ready: bool = True, alexa_id: str = None):
        try:
            completed = self._http_complete_list_item(item, alexa_id=alexa_id)
            if refresh_result:
                refreshed = self.get_alexa_list(False)
                logger.info(
                    "Alexa HTTP complete result for '%s': %s",
                    item,
                    json.dumps(self._compact_items_for_log(refreshed), ensure_ascii=False),
                )
                return refreshed
            return None if completed else None
        except Exception as http_error:
            logger.warning(f"Alexa HTTP complete failed for '{item}', falling back to Selenium: {http_error}")

        if ensure_page_ready:
            self._prepare_alexa_list_page(False)

        element = self._get_alexa_list_item_element(item, ensure_page_ready=False)
        if element is None:
            if refresh_result:
                return self.get_alexa_list(False)
            return None

        toggle = self._find_completion_toggle(element)
        self.driver.execute_script("arguments[0].click();", toggle)
        self._wait_for_element_staleness(element)

        if refresh_result:
            return self.get_alexa_list(False)
        return None


    def add_alexa_list_item(self, item: str, include_details: bool = False):
        return self._add_alexa_list_item(item, refresh_result=True, include_details=include_details)


    def _add_alexa_list_item(self, item: str, refresh_result: bool = True, ensure_page_ready: bool = True, include_details: bool = False):
        try:
            logger.info(f"Alexa add requested: {item}")
            added_item = self._http_add_list_item(item)
            if refresh_result:
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
            if include_details:
                return {
                    "list": None,
                    "added_items": [added_item] if added_item is not None else [],
                }
            return None
        except Exception as http_error:
            logger.warning(f"Alexa HTTP add failed for '{item}', falling back to Selenium: {http_error}")

        if ensure_page_ready:
            self._prepare_alexa_list_page(False)

        logger.info(f"Alexa add requested: {item}")
        self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'add-symbol').click()

        textfield = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'input-box').find_element(By.TAG_NAME, 'input')
        textfield.send_keys(item)

        submit = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'add-to-list').find_element(By.TAG_NAME, 'button')
        submit.click()

        cancel_button = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'cancel-input')
        cancel_button.click()
        self._wait_for_element_staleness(cancel_button)

        if refresh_result:
            refreshed = self.get_alexa_list(False)
            logger.info(f"Alexa add result for '{item}': {json.dumps(refreshed, ensure_ascii=False)}")
            if include_details:
                return {
                    "list": refreshed,
                    "added_items": [],
                }
            return refreshed
        if include_details:
            return {
                "list": None,
                "added_items": [],
            }
        return None


    def update_alexa_list_item(self, old: str, new: str, alexa_id: str = None):
        return self._update_alexa_list_item(old, new, refresh_result=True, alexa_id=alexa_id)


    def _update_alexa_list_item(self, old: str, new: str, refresh_result: bool = True, ensure_page_ready: bool = True, alexa_id: str = None):
        try:
            updated = self._http_update_list_item(old, new, alexa_id=alexa_id)
            if refresh_result:
                return self.get_alexa_list(False)
            return None if updated else None
        except Exception as http_error:
            logger.warning(f"Alexa HTTP update failed for '{old}' -> '{new}', falling back to Selenium: {http_error}")

        if ensure_page_ready:
            self._prepare_alexa_list_page(False)

        element = self._get_alexa_list_item_element(old, ensure_page_ready=False)
        if element == None:
            if refresh_result:
                return self.get_alexa_list(False)
            return None

        element.find_element(By.CLASS_NAME, 'item-actions-1').find_element(By.TAG_NAME, 'button').click()

        textfield = element.find_element(By.CLASS_NAME, 'input-box').find_element(By.TAG_NAME, 'input')
        textfield.clear()
        textfield.send_keys(new)

        element.find_element(By.CLASS_NAME, 'item-actions-2').find_element(By.TAG_NAME, 'button').click()
        self._wait_for_element_staleness(element)

        if refresh_result:
            return self.get_alexa_list(False)
        return None


    def remove_alexa_list_item(self, item: str):
        return self._remove_alexa_list_item(item, refresh_result=True)


    def _remove_alexa_list_item(self, item: str, refresh_result: bool = True, ensure_page_ready: bool = True):
        try:
            removed = self._http_remove_list_item(item)
            if refresh_result:
                return self.get_alexa_list(False)
            return None if removed else None
        except Exception as http_error:
            logger.warning(f"Alexa HTTP delete failed for '{item}', falling back to Selenium: {http_error}")

        # In large lists, items towards the end are sometimes not found on the first try
        # In cases like these, retry if the element is not found
        if ensure_page_ready:
            self._prepare_alexa_list_page(False)

        retries = 3
        while retries > 0:
            element = self._get_alexa_list_item_element(item, ensure_page_ready=False)
            
            if element is None:
                if refresh_result:
                    return self.get_alexa_list(False)
                return None
            
            try:
                # Find the delete button and click it
                delete_button = element.find_element(By.CLASS_NAME, 'item-actions-2').find_element(By.TAG_NAME, 'button')
                delete_button.click()
                self._wait_for_element_staleness(element)
                break
            except StaleElementReferenceException:
                retries -= 1
                time.sleep(1)
            except Exception as e:
                return None

        if refresh_result:
            return self.get_alexa_list(False)
        return None


    def bulk_apply_alexa_list_changes(self, add_items=None, remove_items=None, update_items=None, complete_items=None, include_details: bool = False):
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
        self._prepare_alexa_list_page(False)

        added_items_result = []
        for item in add_items:
            logger.info(f"Alexa bulk add item: {item}")
            added = self._add_alexa_list_item(item, refresh_result=False, ensure_page_ready=False, include_details=True)
            if isinstance(added, dict):
                added_items_result.extend(added.get("added_items", []))

        for item in remove_items:
            logger.info(f"Alexa bulk remove item: {item}")
            self._remove_alexa_list_item(item, refresh_result=False, ensure_page_ready=False)

        for update in update_items:
            logger.info(
                "Alexa bulk update item: %s",
                json.dumps(update, ensure_ascii=False),
            )
            self._update_alexa_list_item(
                update['old'],
                update['new'],
                refresh_result=False,
                ensure_page_ready=False,
                alexa_id=update.get('alexa_id')
            )

        for item in complete_items:
            logger.info(f"Alexa bulk complete item: {item}")
            if isinstance(item, dict):
                self._complete_alexa_list_item(
                    item.get("name") or "",
                    refresh_result=False,
                    ensure_page_ready=False,
                    alexa_id=item.get("alexa_id"),
                )
            else:
                self._complete_alexa_list_item(item, refresh_result=False, ensure_page_ready=False)

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

    # ============================================================
