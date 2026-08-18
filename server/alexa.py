#!/usr/bin/env python3

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
import time
import json
import os
import logging

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
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
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
        chrome_options = Options()
        # Keep headless mode always enabled inside containerized environments.
        # Debug mode only controls verbosity/log output, not headed execution.
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("window-size=1366,768")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument(f"--user-agent={self.user_agent}")

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


    # ============================================================
    # Authentication


    def requires_login(self):
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
        last_text = None
        max_scrolls = 50
        scroll_count = 0

        while True:
            try:
                list_items = list_container.find_elements(By.CLASS_NAME, 'item-title')
                for item in list_items:
                    text = item.get_attribute('innerText')
                    if text and text not in found:
                        found.append(text)

                current_last_text = list_items[-1].get_attribute('innerText') if list_items else None
                if not list_items or current_last_text == last_text:
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
        self._prepare_alexa_list_page(refresh)
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

        return found


    def _parse_getlistitems_response(self, data):
        shopping_list = None

        for list_data in data.values():
            if not isinstance(list_data, dict):
                continue

            list_info = list_data.get("listInfo") or {}
            if list_info.get("listType") == "SHOPPING_LIST" or list_info.get("defaultList") is True:
                shopping_list = list_data
                break

        if shopping_list is None:
            raise InvalidListStateError("Alexa shopping-list API response did not include a shopping list")

        items = []
        for item in shopping_list.get("listItems") or []:
            item_id = item.get("id")
            name = item.get("value")
            list_id = item.get("listId")

            if not item_id or not name or not list_id:
                continue

            items.append({
                "id": item_id,
                "list_id": list_id,
                "name": name,
                "complete": bool(item.get("completed")),
                "version": item.get("version"),
                "created_at": item.get("createdDateTime"),
                "updated_at": item.get("updatedDateTime"),
            })

        return items


    def get_alexa_list_items(self):
        self._prepare_alexa_list_page(refresh=True)
        self.driver.set_script_timeout(45)

        response = self.driver.execute_async_script("""
            const done = arguments[arguments.length - 1];
            fetch('/alexashoppinglists/api/getlistitems', {
                credentials: 'include',
                headers: {
                    'Accept': 'application/json, text/plain, */*'
                }
            })
            .then(async response => done({
                ok: response.ok,
                status: response.status,
                contentType: response.headers.get('content-type'),
                url: response.url,
                body: await response.text()
            }))
            .catch(error => done({
                ok: false,
                error: String(error)
            }));
        """)

        if not isinstance(response, dict):
            raise InvalidListStateError("Alexa shopping-list API fetch returned an invalid response")

        body = response.get("body") or ""
        if response.get("ok") is not True:
            raise InvalidListStateError(
                "Alexa shopping-list API fetch failed: "
                f"status={response.get('status')} "
                f"error={response.get('error')} "
                f"body={body[:200]}"
            )

        content_type = response.get("contentType") or ""
        if "application/json" not in content_type:
            raise InvalidListStateError(
                f"Alexa shopping-list API returned non-JSON content type: {content_type}"
            )

        return self._parse_getlistitems_response(json.loads(body))


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


    def add_alexa_list_item(self, item: str):
        return self._add_alexa_list_item(item, refresh_result=True)


    def _add_alexa_list_item(self, item: str, refresh_result: bool = True, ensure_page_ready: bool = True):
        if ensure_page_ready:
            self._prepare_alexa_list_page(False)

        element = self._get_alexa_list_item_element(item, ensure_page_ready=False)
        if element != None:
            if refresh_result:
                return self.get_alexa_list(False)
            return None

        self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'add-symbol').click()

        textfield = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'input-box').find_element(By.TAG_NAME, 'input')
        textfield.send_keys(item)

        submit = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'add-to-list').find_element(By.TAG_NAME, 'button')
        submit.click()

        cancel_button = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'cancel-input')
        cancel_button.click()
        self._wait_for_element_staleness(cancel_button)

        if refresh_result:
            return self.get_alexa_list(False)
        return None


    def update_alexa_list_item(self, old: str, new: str):
        return self._update_alexa_list_item(old, new, refresh_result=True)


    def _update_alexa_list_item(self, old: str, new: str, refresh_result: bool = True, ensure_page_ready: bool = True):
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


    def bulk_apply_alexa_list_changes(self, add_items=None, remove_items=None, update_items=None):
        add_items = add_items or []
        remove_items = remove_items or []
        update_items = update_items or []

        self._prepare_alexa_list_page(False)

        for item in add_items:
            self._add_alexa_list_item(item, refresh_result=False, ensure_page_ready=False)

        for item in remove_items:
            self._remove_alexa_list_item(item, refresh_result=False, ensure_page_ready=False)

        for update in update_items:
            self._update_alexa_list_item(update['old'], update['new'], refresh_result=False, ensure_page_ready=False)

        return self.get_alexa_list(False)

    # ============================================================
