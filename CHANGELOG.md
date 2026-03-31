# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/).


## [2603.090.06] - 2026-03-31

### Bug Fixes
- **Alexa reopen after empty snapshot** - Fixed a follow-up bidirectional sync edge case where an item re-added to Alexa could still be completed again if the previous Alexa snapshot was empty. Active Alexa items now always reopen matching completed items in Home Assistant before completion decisions are made.

## [2603.090.05] - 2026-03-31

### Bug Fixes
- **Alexa re-add reopening** - Fixed a bidirectional sync regression where an item completed in Home Assistant could be immediately re-completed on the next sync even after being re-added to Alexa. Items that newly reappear in Alexa's active list now reopen in Home Assistant instead.

## [2603.090.04] - 2026-03-31

### Features
- **Bidirectional completion sync** - The Home Assistant component now treats items that disappear from the Alexa active list as completed remotely and keeps them completed in Home Assistant instead of re-adding them to Amazon.

### Improvements
- **Alexa checkbox completion** - Home Assistant completions now use the Alexa shopping list completion control instead of deleting items outright, preserving Amazon's completed-items behavior.
- **Sync state snapshot** - Added a lightweight local sync metadata snapshot so the integration can detect recent removals from Alexa's active list between sync runs.

## [2603.090.03] - 2026-03-31

### Bug Fixes
- **False empty Alexa reads** - Added an explicit wait for shopping list items to hydrate before accepting an empty Alexa list, preventing Home Assistant from misreading populated lists as empty after the bulk performance changes.

### Improvements
- **Safer post-load extraction** - Alexa list reads and item lookups now retry once after hydration before treating the list as truly empty.

## [2603.090.02] - 2026-03-31

### Bug Fixes
- **WebSocket keepalive during long syncs** - Moved blocking Selenium shopping list operations off the asyncio event loop so bulk list changes no longer trigger `keepalive ping timeout` disconnects.

### Improvements
- **DOM-driven list waits** - Replaced the fixed five-second waits around Alexa list loading with explicit page readiness checks before reading or mutating the list.
- **Single page preparation for bulk operations** - Bulk add/remove/update flows now prepare the Alexa shopping list page once and reuse it across all item mutations.

### Performance
- **Faster bulk mutations** - Removed per-item five-second delays from bulk shopping list operations, reducing the time needed for larger batches.

## [2603.090.01] - 2026-03-31

### Improvements
- **Bulk sync session reuse** - When Home Assistant needs to add or remove multiple items in one sync, the custom component now sends a single bulk command so the server can reuse one Chromium session for the whole batch.
- **Bulk server command** - Added a `bulk_apply_changes` server command that applies grouped add/remove/update operations and refreshes the Alexa list only once at the end.

### Performance
- **Reduced browser churn** - Bulk shopping list changes no longer open and close Chromium once per item, significantly reducing sync time for larger batches.

## [2603.090.00] - 2026-03-31

### Bug Fixes
- **Empty Alexa list handling** - Fixed an issue where deleting the last remaining Amazon shopping list item could leave a stale cached item in Home Assistant instead of syncing an empty list.
- **Cache update semantics** - Simplified `_update_cached_list()` so the custom component now trusts successful server responses, including valid empty lists.

### Improvements
- **Server-side list validation** - Moved empty-list trust checks into `server/alexa.py`, validating the Alexa shopping list page state before accepting an empty scrape result.
- **Shopping list extraction flow** - Split server-side page validation from list item extraction so ambiguous scrape states raise explicit errors instead of silently returning empty data.

## [2603.079.10] - 2026-03-21

### Features
- **Configurable Chromium debug logging** — Added support for enabling Selenium/Chromium verbose logs and configuring the output path.

### Bug Fixes
- **Add-on env placeholder fallback** — When add-on environment placeholders are not rendered (e.g. literal `{{ ... }}` values), the server now falls back to reading options from `/data/options.json` to correctly resolve debug settings.

### Maintenance
- **Local workspace metadata ignored** — Added `.kilocode/` and `.agent/` to `.gitignore` to prevent accidental commits of local tooling metadata.

## [2603.079.09] - 2026-03-20

### Improvements
- **ChromeDriver Debugging** — Disabled verbose debugging in ChromeDriver as a follow-up to the zombie processes fix. This will be introduced as an optional configuration flag in the Add-on in a future update.

## [2603.079.02] - 2026-03-20

### Features
- **Dev Channel** — Added a dedicated Dev Add-on testing channel to allow side-by-side installation of experimental builds.

### Bug Fixes
- **Chromium crash in Docker** — Fixed "tab crashed" and "session not created" errors by reverting the Docker container to `alpine:3.20` (guaranteeing stable Chromium 131) and replacing `driver.refresh()` with `driver.get()` to bypass Headless Chrome renderer bugs.
- **Zombie processes** — Fixed an issue where defunct Chromium processes would accumulate over time when running the server. Added `tini -s` as an init manager for Docker to reap children under s6-overlay, and ensured the WebDriver cleans up via `.quit()` instead of `.close()`.

## [2602.056.03] - 2026-02-25

### Features
- **Manual Sync Logging** — The custom component now logs an info-level message in Home Assistant when a manual sync action is triggered.
- **Server Logging Improvements** — Added a custom logging format to `server.py` and `alexa.py` to include timestamps in all log lines.
- **Server Startup Banner** — Added a clearer startup log line to distinguish server restarts in the logs.

### Bug Fixes
- **Client Empty Command Crash** — Fixed an `IndexError` in `client.py` that occurred when pressing Enter without typing any command in the console.
- **Selenium Logging on Windows** — Fixed excessive console logging from the Selenium WebDriver on Windows by setting the log level to 3.

## [2602.055.00] - 2026-02-24

### Bug Fixes
- **Chromium Extraction** — Preserved zip file permissions natively when extracting Chromium, ensuring `chrome` and helper binaries are correctly marked as executable on Linux and macOS.

## [2602.054.24] - 2026-02-24

### Bug Fixes
- **Sensor State Updates** — Fixed an issue where the `sensor.alexa_shopping_list_sync` state would show up as `"unknown"` until a change in the list triggered a sync. The sensor now correctly reflects the last successful sync timestamp at startup.

## [2602.054.23] - 2026-02-23
## [2602.054.22] - 2026-02-23

### Bug Fixes
- **Persistent Notification AttributeError** — Replaced `hass.components.persistent_notification` with direct imports to fix an `AttributeError` on session expiration.
- **Binary Sensor State Updates** — The `binary_sensor` now actively pulls the cached authentication state directly from the server during each polling cycle, ensuring it accurately reflects connection status without triggering unnecessary browser sessions.

## [2602.054.04] - 2026-02-23

### Bug Fixes
- **Accurate Client Status** — The `status` command in `client.py` now forces a live check by bypassing the 24-hour cache and actively detecting Amazon session "soft-expiries," ensuring it never falsely reports being authenticated.

### Features
- **Client Status Command** — Added a `status` command to `client.py` to easily check if the backend server is currently authenticated with Amazon without needing to trigger a sync.
## [2602.054.02] - 2026-02-23

### Features
- **Authentication Status Binary Sensor** — Added a new `binary_sensor` to Home Assistant (`binary_sensor.alexa_shopping_list_authenticated`) that shows if the addon is connected or disconnected.
- **Persistent Notifications** — If the shopping list sync fails due to an expired session, a persistent notification will now pop up in Home Assistant alerting you to re-authenticate. The notification automatically clears once you log back in.
## [2602.054.01] - 2026-02-23

### Bug Fixes
- **Graceful handling of expired Amazon sessions** — Detects login redirects (`ap/signin`, `ap/mfa`, etc.) immediately instead of waiting 30s for a `TimeoutException`
- **Server returns clean "Not authenticated" error** — `NotAuthenticatedError` caught in all shopping list commands, auth cache invalidated automatically
- **HA component logs auth errors clearly** — `_get_list` raises exception on server error, logged as `"Sync error: Not authenticated"` in HA logs

### Improvements
- **Added diagnostic logging on Selenium timeout** — Captures screenshot, current URL, and page source snippet when `TimeoutException` occurs


## [2602.050.01] - 2026-02-19

### CI/CD
- **Fixed HASS addon publish step** — `git commit` no longer fails when version is already up to date

## [2602.050.00] - 2026-02-19

### Critical Fixes
- **Fixed `_do_sync()` crash** — `loop` variable was defined in `sync()` but never passed to `_do_sync()`, causing `NameError` and breaking shopping list synchronization
- **Fixed `UnboundLocalError` in `sync()`** — `result` variable was not initialized before the `try/except` block, causing the "result" missing error in the custom component

### Bug Fixes
- **Protected cache from data loss** — `_update_cached_list()` now rejects `None` and empty lists when cache already contains data
- **Added exception handling to all server commands** — Selenium operations wrapped in `try/except/finally`, ensuring browser cleanup on errors
- **Re-enabled exception handling in WebSocket handler** — Catches `JSONDecodeError` and general exceptions gracefully
- **Fixed `_set_config_value` crash** — No longer throws `KeyError` on non-existent config keys
- **Fixed `_route_command` missing returns** — `shutdown` and unknown commands now return proper responses
- **Removed reference to undefined `_cmd_mfa`** — Dead code cleaned up
- **Used `clients.discard()` instead of `clients.remove()`** — Prevents `KeyError` during WebSocket cleanup
- **Replaced `_is_syncing` boolean with `asyncio.Lock()`** — Prevents race conditions in concurrent sync

### Improvements
- **Robust virtual list scrolling** — Handles `StaleElementReferenceException`, compares by text instead of WebElement references, max 50 scroll limit
- **Flexible URL matching** — Uses `in` operator instead of exact `==` for Alexa shopping list URL
- **Unique item IDs** — Generated via MD5 hash instead of space-to-underscore replacement
- **Added proper logging** — Server uses Python `logging` module for error reporting

### CI/CD
- **Tag-based releases** — Workflow only triggers on version tags, not every push to main
- **Updated Docker Hub and addon repo references**
- **Added `workflow_dispatch` trigger** — Allows manual workflow runs from GitHub UI


## [2602.050.00] - 2026-02-19

First release from raidolo fork.
