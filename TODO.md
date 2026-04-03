# TODO

## Milestone 1: Simplify the server

### Remove Selenium from the server

- [ ] Remove Selenium imports and driver lifecycle from `server/alexa.py`
- [ ] Keep only the HTTP/API implementation for auth and list operations
- [ ] Remove Selenium fallback branches from public methods
- [ ] Remove Selenium-only exceptions and helpers that are no longer used
- [ ] Remove production diagnostic code that writes HTTP dumps or mutates the real list for smoke testing
- [ ] Remove `selenium` from `server/requirements.txt`
- [ ] Simplify `server/Dockerfile` to a Python-only runtime
- [ ] Update `README.md` to describe the server as HTTP API-based rather than Selenium-based

### Clean up server lifecycle and state

- [ ] Create a singleton `AlexaShoppingList` instance at server startup
- [ ] Remove per-command `_start_alexa()` / `_stop_alexa()`
- [ ] Remove `alexa` and `alexa_running` globals
- [ ] Encapsulate mutable server state such as `clients`, `config`, and `server`
- [ ] Replace the signal handler shutdown path with a loop-safe async shutdown flow

## Milestone 2: Make sync logic easier to reason about

### Refactor `_do_sync`

- [ ] Split `_do_sync` in `custom_components/alexa_shopping_list/asl.py` into clear phases
- [ ] Isolate state loading, link management, remote change detection, local change detection, Alexa writes, HA writes, and snapshot persistence
- [ ] Reduce the amount of cross-phase mutable state

### Remove unnecessary executor usage

- [ ] Keep executor offloading only for real blocking I/O
- [ ] Run in-memory dict/list transformations inline
- [ ] Re-check sync performance after simplification

### Decide the future of completed-ledger logic

- [ ] Either restore ledger-based behavior fully or remove the dead/disabled branches
- [ ] Remove placeholder lists such as disabled reopen/protection results if they stay unused
- [ ] If the ledger stays, document the intended sync guarantees
- [ ] If the ledger goes, simplify event handling and metadata accordingly

## Milestone 3: Improve transport and API robustness

### Persistent WebSocket connections

- [ ] Reuse a single WebSocket connection in `asl.py`
- [ ] Reuse a single WebSocket connection in `client.py`
- [ ] Add reconnect logic after disconnects
- [ ] Make sure command failures leave the connection in a recoverable state

### Improve HTTP robustness for Amazon APIs

- [ ] Add retries with exponential backoff for transient failures
- [ ] Handle timeouts and connection errors explicitly
- [ ] Handle HTTP 429 and honor `Retry-After` when present
- [ ] Make request timeout configurable

## Milestone 4: Home Assistant and client cleanup

### Home Assistant integration cleanup

- [ ] Stop relying on internal `hass.data["shopping_list"]` access if a stable HA API is available
- [ ] Unregister services during `async_unload_entry`
- [ ] Add `DeviceInfo` to sensor and binary sensor entities
- [ ] Extract shared auth notification logic into one helper

### Client and config flow cleanup

- [ ] Preserve case in CLI arguments while still lowercasing the command verb
- [ ] Make `_handle_commands` return early or use `elif`
- [ ] Add `api_token` support to the config flow when WebSocket auth is introduced
- [ ] Make validation and boolean checks more idiomatic across `client.py` and `config_flow.py`

### Naming and general Python cleanup

- [ ] Rename `_cmd_get_add_shopping_list_item` to `_cmd_add_shopping_list_item`
- [ ] Rename `_cmd_get_update_shopping_list_item` to `_cmd_update_shopping_list_item`
- [ ] Rename `_cmd_get_remove_shopping_list_item` to `_cmd_remove_shopping_list_item`
- [ ] Rename `_cmd_get_complete_shopping_list_item` to `_cmd_complete_shopping_list_item`
- [ ] Replace `_route_command` if-chain with a dispatch table
- [ ] Remove `== True` / `== False` / `!= None` patterns
- [ ] Remove mutable default arguments
- [ ] Replace string concatenation logs with f-strings
- [ ] Update deprecated UTC datetime usage for Python 3.12+

## Milestone 5: Secure the WebSocket protocol

### Add shared-token authentication

- [ ] Implement an optional shared token for the WebSocket server
- [ ] Accept unauthenticated requests only when no token is configured yet
- [ ] Reject invalid tokens with `{"result": null, "error": "Unauthorized"}`
- [ ] Update the HA integration to store and send the token
- [ ] Update the CLI client to create and send the token during first setup
- [ ] Decide whether the CLI client should persist the token locally

## Milestone 6: Safety net and release confidence

### Add focused tests

- [ ] Add protocol-level tests for WebSocket commands and token auth
- [ ] Add unit tests for server config handling
- [ ] Add targeted sync tests for key edge cases
- [ ] Add regression coverage for completed items deleted in HA before the next sync

### Define manual verification flows

- [ ] Login still works
- [ ] Initial sync still works
- [ ] Add, update, remove, and complete flows still work
- [ ] Auth expiry is surfaced correctly in Home Assistant
