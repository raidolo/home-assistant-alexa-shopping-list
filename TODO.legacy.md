# TODO

## Critical

### Remove Selenium from `alexa.py` (server)

- [ ] Remove all Selenium imports (lines 3-10)
- [ ] Remove `WAIT_TIMEOUT` (only used by Selenium waits; HTTP timeout is handled separately)
- [ ] Remove `_setup_driver()`, `_clear_driver()`, `__del__`
- [ ] Simplify `__init__` to just `amazon_url`, `cookies_path`, `is_authenticated`
- [ ] Remove `InvalidListStateError` (only used by the Selenium path)
- [ ] Remove all Selenium methods:
  - `_selenium_wait_element`, `_selenium_wait_page_ready`, `_selenium_get`
  - `_load_cookies` (the one using `driver.add_cookie`)
  - `_ensure_driver_is_on_alexa_list`, `_prepare_alexa_list_page`
  - `_wait_for_alexa_list_ready`, `_wait_for_alexa_list_items`
  - `_get_alexa_list_container`, `_extract_alexa_list_items`
  - `_get_alexa_list_item_element`, `_find_completion_toggle`
  - `_validate_empty_alexa_list_result`, `_wait_for_element_staleness`
  - `_check_auth_redirect`
  - `_debug_dump_getlistitems_api` (the one using `execute_async_script`)
  - `save_session` (saves cookies from the Selenium driver)
- [ ] Remove diagnostic/test code from production:
  - `_debug_dump_getlistitems_api_http` (HTTP dump to /tmp)
  - `run_http_api_startup_smoke_test` (adds/modifies/deletes real items from the list for testing)
  - `_http_dump_dir`, `_http_dump_path`, `_http_write_dump` (helpers only used by the smoke test)
- [ ] Evaluate removal of Selenium-only helpers:
  - `_is_debug_mode`, `_debug_log_path` → only used to configure Chromium logs
  - `_load_addon_options`, `_get_addon_option`, `_is_template_literal` → if only used by `_is_debug_mode`
  - `_get_file_location` → check if still needed outside the Selenium context
- [ ] Simplify public methods by removing try/except with Selenium fallback:
  - `requires_login()` → call `_http_requires_login()` directly
  - `get_alexa_list()` → call `_get_alexa_list_http()` directly
  - `_add_alexa_list_item()` → call `_http_add_list_item()` directly
  - `_update_alexa_list_item()` → call `_http_update_list_item()` directly
  - `_remove_alexa_list_item()` → call `_http_remove_list_item()` directly
  - `_complete_alexa_list_item()` → call `_http_complete_list_item()` directly
- [ ] Remove the `get_alexa_list()` fallback path that, after HTTP failure:
  - Prepares the Selenium page
  - Runs two diagnostic dumps (HTTP and JS `execute_async_script`)
  - Scrapes the virtual-list with retry
  - Scrolls back to top
  - Returns items without `id` (name only)
- [ ] Remove `selenium` from `server/requirements.txt`
- [ ] Update the Dockerfile:
  - Remove `RUN apk add chromium`
  - Remove `RUN apk add chromium-chromedriver`
  - Remove `ENV CHROME_DRIVER="/usr/bin/chromedriver"`
  - Remove `RUN rm -rf /var/lib/apt/lists/*` (wrong: this is an apt/Debian cleanup, but the image is Alpine with apk)
  - Collapse remaining `RUN apk add` into a single layer: `RUN apk add --no-cache python3 py3-pip tini`
- [ ] Update `server.py`:
  - Remove the `_start_alexa()` / `_stop_alexa()` pattern that creates and destroys the instance for every command
  - Create a singleton `AlexaShoppingList` instance at server boot (without Chrome it's lightweight)
  - Remove the `alexa` and `alexa_running` globals
  - Command handlers use the singleton instance directly without start/stop
- [ ] Update addon options (`config.yaml` in hass repo):
  - Evaluate removal of `ALEXA_SHOPPING_LIST_DEBUG` and `ALEXA_SHOPPING_LIST_DEBUG_LOG_PATH` (they configure Chromium logs)
  - If a debug mode is still desired, reconnect it to the server's Python logging
- [ ] Update README.md:
  - Server description says "Selenium-based Python app" → update to "HTTP API-based"
  - Remove references to Chromium/headless browser

## High

### WebSocket server authentication

- [ ] Implement a shared token for the WebSocket server
- [ ] Flow: the client generates the token on first setup, sends it to the server via `config_set`
- [ ] Server (`_process_command` in `server.py`):
  - Read the token from `config.json` (key `api_token`)
  - If token is present, verify that every WebSocket message contains a matching `token` field
  - If token is absent, accept everything (first-boot setup mode)
  - Reject with error `{"result": null, "error": "Unauthorized"}` if token is invalid
- [ ] Custom component (`config_flow.py`):
  - Add an `api_token` field in the `server` step with password type (hidden in the UI)
  - Save it in the config entry
  - Pass it to `AlexaShoppingListSync` which includes it in every `_send_command`
- [ ] Client (`client.py`):
  - On first setup (`_check_server`): generate a random token, save it to the server via `config_set`, print it to screen
  - For re-authentication: ask the user for the token or accept it as a CLI argument (`--token`)
  - Include the token in every `_send_command`
  - Evaluate saving the token locally in a file to avoid re-entry

### Refactor global state in `server.py`

- [ ] Encapsulate global state (`clients`, `config`, `server`) in a class or module
  - Currently `alexa`, `alexa_running`, `clients`, `config`, `server` are all mutable globals
  - With Selenium removal and the singleton, `alexa` and `alexa_running` disappear
  - The remaining ones (`clients`, `config`, `server`) should be encapsulated
- [ ] `_signal_handler` uses `asyncio.run()` inside a signal handler, which is problematic
  - Replace with `loop.call_soon_threadsafe()` or `asyncio.ensure_future()`

## Medium

### Inconsistent naming in `server.py`

- [ ] Rename `_cmd_get_add_shopping_list_item` → `_cmd_add_shopping_list_item`
- [ ] Rename `_cmd_get_update_shopping_list_item` → `_cmd_update_shopping_list_item`
- [ ] Rename `_cmd_get_remove_shopping_list_item` → `_cmd_remove_shopping_list_item`
- [ ] Rename `_cmd_get_complete_shopping_list_item` → `_cmd_complete_shopping_list_item`
- [ ] Refactor `_route_command` from `if` chain to dispatch dict:
  ```python
  COMMANDS = {
      "config_valid": _cmd_config_valid,
      "config_set": _cmd_config_set,
      "get_list": _cmd_get_shopping_list,
      # ...
  }
  handler = COMMANDS.get(command)
  if handler:
      return await handler(arguments)
  return None, f"Unknown command: {command}"
  ```

### `asl.py` complexity

- [ ] Break down `_do_sync` (~400 lines) into distinct phases:
  - `_sync_phase_load_state()` - load previous and current snapshots
  - `_sync_phase_link_items()` - manage ha<->alexa item links
  - `_sync_phase_detect_remote_changes()` - detect changes from Alexa
  - `_sync_phase_detect_local_changes()` - detect changes from HA
  - `_sync_phase_apply_to_alexa()` - apply changes to Alexa
  - `_sync_phase_apply_to_ha()` - apply changes to HA
  - `_sync_phase_save_state()` - save updated snapshots
- [ ] Remove unnecessary `run_in_executor` calls for in-memory dict operations (not I/O blocking):
  - `_prune_item_links`, `_bootstrap_item_links`
  - `_filter_unlinked_alexa_active_names`, `_filter_unlinked_ha_items`
  - `_apply_remote_linked_changes`, `_remote_linked_missing_or_completed_items`
  - `_collect_linked_local_completions`
  - `_strip_names_from_ha_list`, `_filter_alexa_items`
  - `_merge_ha_with_alexa`, `_dedupe_ha_items_by_id`
  - `_mark_items_completed_from_count_delta`
  - `_remap_links_to_applied_ha_ids`, `_link_added_alexa_items`
  - `_update_completed_ledger`, `_get_completed_ledger`
  - `_get_previous_alexa_snapshot`, `_get_previous_ha_items`
  - `_set_sync_snapshot`, `_set_item_links`
  - These are all operations on in-memory dicts/lists, the CPU cost is negligible

### Persistent WebSocket connection

- [ ] `asl.py` (`_send_command`): replace per-command `async with websockets.connect()` with a persistent connection
  - Keep `self._ws` as an attribute
  - Add automatic reconnection logic if the connection drops
  - Especially important in `_do_sync` where many commands are issued in sequence
- [ ] `client.py` (`_send_command`): same refactor

### Dead code and disabled features

- [ ] Decide whether to remove or finalize disabled features in `asl.py`:
  - Line 1360-1363: `alexa_reopened_in_remote = []` (Alexa-driven reopen disabled)
  - Line 1388-1391: `protected_alexa_items = []` (ledger-based protection disabled)
  - Line 1549-1550: `protected_refreshed_items = []` (ledger sync results disabled)
- [ ] If the completed_ledger no longer affects sync results, evaluate removing all related logic:
  - `_get_completed_ledger`, `_set_completed_ledger`, `_update_completed_ledger`
  - `_prune_completed_ledger`, `_completed_ledger_cutoff`, `COMPLETED_LEDGER_TTL_HOURS`
  - `_mark_ledger_item_seen_on_alexa`, `_protected_alexa_items_from_ledger`
  - `_clear_ledger_for_active_ha_items`
  - `_handle_homeassistant_shopping_list_event` (if only used for the ledger)
  - The `ha_completed_ledger` key in sync metadata
- [ ] Remove the comment in `__init__.py` line 40: `# hass.bus.async_listen(...)`
- [ ] `__init__.py` line 43: the `log_homeassistant_shopping_list_event` listener also calls `_handle_homeassistant_shopping_list_event` which feeds the ledger → if the ledger is removed, simplify the listener

### Duplicated auth notification logic

- [ ] `sensor.py` (lines 61-69) and `__init__.py` (lines 84-92) contain the same identical block for creating/dismissing the persistent_notification for expired auth
  - Extract into a shared helper, e.g. `_update_auth_notification(hass, is_authenticated)`

### Python style

Changes in `server.py`:
- [ ] `_get_config_value`: `if key in config.keys():` → `if key in config:`
- [ ] `_set_config_value`: `if new_value != None:` → `if new_value is not None:`
- [ ] `_set_config_value`: string concatenation `"Set config value `"+key+"` = "+str(new_value)` → f-string
- [ ] `_start_alexa`: `if alexa_running == False:` → `if not alexa_running:`
- [ ] `_stop_alexa`: `if alexa_running == True:` → `if alexa_running:`
- [ ] `_cmd_is_authenticated`: `if requires_login == True:` → `if requires_login:`
- [ ] `_route_command`: `arguments={}` as mutable default → `arguments=None` + `arguments = arguments or {}`
- [ ] `main()`: string concatenation `"...started on port "+str(listen_port)` → f-string

Changes in `alexa.py`:
- [ ] Line 263: `datetime.datetime.utcnow()` → `datetime.datetime.now(datetime.timezone.utc)` (deprecated in Python 3.12)
- [ ] `_selenium_get`: `if wait_for_element != None:` → `if wait_for_element is not None:`
- [ ] `_cookie_cache_path`: `if self.cookies_path != "":` → `if self.cookies_path:`

Changes in `asl.py`:
- [ ] `_command_successful`: `response['error'] != None` → `response['error'] is not None`
- [ ] `_cached_list_needs_updating`: `if self.last_updated == None:` → `if self.last_updated is None:`
- [ ] `_collect_local_updates`: `if item.get('complete') == True:` → `if item.get('complete'):`
- [ ] `sync()`: `if os.path.exists(...) == False:` → `if not os.path.exists(...):`
- [ ] `sync()`: `if force == False:` → `if not force:`
- [ ] `_debug_log_entry`: `if logger == None:` → `if logger is None:`
- [ ] `is_authenticated == False` in `requires_login` fallback → `not self.is_authenticated`

Changes in `client.py`:
- [ ] `_command_successful`: `response['error'] != None` → `response['error'] is not None`
- [ ] `_ping_server`: `if connected == False:` → `if not connected:`
- [ ] `_server_config_valid`: `== True` → remove
- [ ] `_server_authenticated`: `== True` → remove
- [ ] `_cmd_config_set`: `if announce == True:` → `if announce:`
- [ ] `_command_result`: `found_value == None` → `found_value is None`
- [ ] `_handle_commands`: `if` chain without `elif` → every condition is checked even after a match; use `elif` or `return` after each branch

Changes in `config_flow.py`:
- [ ] `async_step_server`: `== True` repeated 3 times → remove
- [ ] `async_step_sync_mins`: `sync_mins == "" or sync_mins == None` → `not sync_mins`

Changes in `authenticator.py`:
- [ ] `_selenium_get`: `if wait_for_element != None:` → `if wait_for_element is not None:`

## Low

### Client

- [ ] `client.py` line 349: `.strip().lower()` lowercases the arguments too
  - Change to: lowercase only `parts[0]` (the command), leave `parts[1:]` untouched
  - Currently `add Parmigiano Reggiano` becomes `add parmigiano reggiano`
- [ ] `authenticator.py`: Chromium download from Google does not verify checksums of downloaded files
- [ ] `_handle_commands` in `client.py`: `if` chain without `elif`/`return` checks every condition even after a match

### HTTP call robustness for Amazon APIs

- [ ] Add retry with exponential backoff in `_http_request_json`
  - At least for transient errors (5xx, timeout, connection error)
  - E.g. 3 attempts with delay 1s, 2s, 4s
- [ ] Add specific handling for HTTP 429 (Too Many Requests)
  - Respect the `Retry-After` header if present
- [ ] Make the timeout configurable (currently hardcoded `WAIT_TIMEOUT=30`)
  - Parameter in `config.json` or environment variable
- [ ] `_http_request_json` does not handle network errors (`urllib.error.URLError`, `ConnectionError`, `TimeoutError`)
  - Currently only `urllib.error.HTTPError` is caught; connection errors propagate as unhandled exceptions

### Home Assistant integration

- [ ] Remove direct access to `hass.data["shopping_list"].async_load` in `__init__.py` line 31
  - Accesses internal HA data structures that may change between versions
  - The rest of the code already uses `todo.*` APIs for CRUD operations
- [ ] Unregister services in `async_unload_entry` (currently `SERVICE_SYNC` is not removed on reload)
- [ ] Add `DeviceInfo` to sensor and binary_sensor entities to group them in the HA device registry

### Sync edge case (from DEVELOPMENT.md)

- [ ] Handle the case where completed items are deleted from HA before the next sync
  - Currently the integration may re-import them from Alexa as new additions
  - This was the original plan for the completed_ledger, which is now disabled
