# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/).


## [2604.003.04] - 2026-04-05

### Features
- **Modern browser backend with Playwright** - The server now moves away from the old Selenium-era architecture and starts modernizing around a dedicated Playwright browser backend that executes Alexa shopping list API calls from a real browser context.

### Improvements
- **Native Python API path retired** - The short-lived `urllib` / native Python HTTP request path to Amazon has been removed from the server after real-world testing showed that Amazon starts rejecting those direct API calls very quickly, even when valid session cookies are present.
- **Browser-context API execution** - Alexa list reads and mutations are now routed through browser-side `fetch()` calls executed inside Playwright, keeping cookies and request context attached to an actual browser session instead of raw Python HTTP traffic.
- **Cleaner server modularity** - Playwright lifecycle and browser request handling now live in a dedicated `server/browser_backend.py` module so `server.py` and `server/alexa.py` stay focused on server orchestration and the public Alexa-list facade.

### Notes
- **This is a strategic rollback from HTTP-only server access** - The pure Python API approach delivered excellent performance, but Amazon began returning anti-automation blocks and `503` responses after short periods of use, making that route unreliable for real-world operation.
- **The goal is modernization, not a return to old DOM scraping** - The new direction keeps the server browser-based where needed, but aims to use Playwright plus browser-context API calls rather than rebuilding the previous Selenium DOM-driven flow.

## [2604.003.03] - 2026-04-04

### Improvements
- **Encapsulated WebSocket server state** - Refactored the server internals around an explicit `ServerApp` so connection tracking, config state, backend lifecycle, and command routing are no longer spread across mutable module globals.
- **Safer async shutdown flow** - The server shutdown path now schedules cleanup on the running event loop instead of calling `asyncio.run()` from the signal handler.

## [2604.003.02] - 2026-04-03

### Bug Fixes
- **Retry truncated Alexa HTTP responses** - The server now retries transient Amazon HTTP failures such as `IncompleteRead`, timeouts, connection errors, and temporary `429`/`5xx` responses instead of failing the request immediately.

### Improvements
- **More resilient cookie-based API reads** - Alexa list fetches are now less likely to surface one-off transport glitches to the CLI or Home Assistant when Amazon closes or interrupts a response mid-transfer.

## [2604.003.01] - 2026-04-03

### Improvements
- **Server is now HTTP-only after login** - Removed Selenium and Chromium from the server runtime so Alexa list reads and mutations now rely entirely on Amazon's HTTP shopping list endpoints once session cookies are available.
- **Lighter server lifecycle** - The WebSocket server now keeps a shared `AlexaShoppingList` instance instead of creating and tearing it down for every command, which better matches the new lightweight HTTP-only backend.
- **Slimmer container image** - The server Docker image no longer installs Chromium or chromedriver and now only ships the Python runtime pieces needed for the HTTP-based server.
- **Docs aligned with reality** - Updated the project README so the server description reflects the HTTP cookie-based architecture rather than the old Selenium-based one.

### Notes
- **Desktop login flow is unchanged** - The desktop authenticator still uses a real browser to obtain Amazon session cookies; this change only removes Selenium from the long-running server component.
- **GitHub Actions should validate the runtime path** - This change was syntax-checked locally, while Docker build/runtime validation is intentionally deferred to CI for this branch.

## [2604.003.00] - 2026-04-03

### Features
- **Home Assistant primitive refactor** - The custom component now moves away from direct `.shopping_list.json` reconstruction and uses Home Assistant-native `todo.*` APIs as the foundation for reading and applying shopping list changes.
- **Amazon HTTP API discovery and adoption** - The integration now reads Alexa shopping list data from Amazon's internal `alexashoppinglists` HTTP API instead of relying on DOM scraping for the main read path.
- **HTTP CRUD support for Alexa list operations** - Add, rename/update, complete, and delete operations now use Amazon's HTTP endpoints (`addlistitem`, `updatelistitem`, `deletelistitem`) as the primary execution path.
- **Structured Alexa item model** - Alexa list items are now handled as real structured objects with stable `id`, `completed`, `createdDateTime`, `updatedDateTime`, `version`, and `listId` fields.
- **Count-based duplicate handling** - Bidirectional sync now treats repeated identical names as counted occurrences, allowing repeated items to be added, completed, and re-imported more predictably across Home Assistant and Alexa.
- **First step toward id-first sync** - The sync metadata now starts persisting `ha_id -> alexa_id` links so linked items can be handled by stable identity instead of name-only heuristics.

### Improvements
- **Instant sync feel** - Normal sync runs are now dramatically faster because the hot path no longer has to drive Chromium through list reads and item mutations.
- **New remote item import** - Items newly added on Alexa are now imported into Home Assistant without being mistaken for stale completed entries that should be removed.
- **Alexa active-count reconciliation** - Alexa completion decisions are now based on the delta between active items on both sides, avoiding false removals when Home Assistant already has completed history for the same name.
- **Reliable completed-state visibility** - The sync can now read Amazon's real completed state directly from the API instead of inferring completions only from active-list disappearance.
- **Reliable remote rename detection** - When an Alexa item keeps the same `id` and changes `value`, the sync now has a trustworthy way to recognize that change as a real rename rather than a delete-plus-add guess.
- **Default shopping list filtering** - HTTP reads now ignore non-default Amazon lists returned in the same payload, preventing unrelated archived/shared/secondary list content from leaking into Home Assistant.
- **HTTP-based authentication validation** - Authentication checks now probe the Alexa list HTTP API directly, treating `401` / `AuthenticationFailure` as real session expiry and reducing false "Not authenticated" reports caused by old Selenium-only checks.
- **Refreshed-delta handling** - Follow-up Alexa refreshes no longer reapply the same completion delta back into Home Assistant after a locally initiated completion sync.
- **Safer rename routing** - Local Home Assistant renames can now target the correct Alexa item by `alexa_id` once a link exists, instead of always relying on ambiguous name matching.
- **Immediate add-response linking** - New items created on Alexa from Home Assistant can now be linked immediately from the HTTP `addlistitem` response instead of waiting for a later name-based bootstrap.
- **Safer linked/unlinked reconciliation** - Stale or semantically invalid links no longer suppress required fallback sync operations for duplicate items.
- **Stable merged HA output** - The final Home Assistant list merge now deduplicates by Home Assistant item ID, preventing duplicate local records with the same ID from being reintroduced during sync.

### Notes
- **Authentication flow is unchanged for users** - Login still happens through the desktop client by launching a real browser, completing Amazon sign-in there, and saving the resulting cookies to the server. The big change is what happens after login: once cookies exist, sync traffic is now mostly HTTP API driven instead of browser driven.
- **Selenium is no longer on the hot path** - Chromium/Selenium now mostly remains as a bootstrap/fallback layer for authentication and emergency compatibility. The normal list mirror path is primarily HTTP-based.
- **One-sync completion protection is no longer a primary feature** - The old historical protection model now plays a much smaller role. Linked items increasingly sync by stable identity, while unlinked edge cases still use limited count-based fallback logic during the transition.
- **Duplicate re-adds are consumed one occurrence at a time** - If an identical item is added again on Alexa between sync runs, only one occurrence is removed on the next completion-propagation sync, and any remaining occurrence is re-imported into Home Assistant on the following sync.
- **Linked items are now treated more precisely** - Once a Home Assistant item and an Alexa item are linked by stable ID, rename and completion handling can bypass some of the older name/count heuristics.
- **The sync is still in a transitional hybrid phase** - Linked items increasingly use id-based logic, while unlinked items still fall back to name/count reconciliation. This is intentional and keeps the refactor safe while migrating real-world lists forward.
- **Client output is cleaner** - The CLI `list` output now shows a compact human-readable view with all active items and a preview of completed items, instead of dumping the full raw JSON payload.

## [2604.001.00] - 2026-04-01

### Bug Fixes
- **Options flow compatibility** - Fixed the custom integration options flow so it no longer assigns `config_entry` manually, avoiding the Home Assistant deprecation warning and preserving compatibility with Home Assistant 2025.12 and later.

## [2603.090.09] - 2026-03-31

### Features
- **Startup sync control** - Added a new integration option to skip the first automatic Alexa shopping list sync after Home Assistant startup.

### Improvements
- **Configurable after setup** - The new startup-sync behavior is exposed through the integration options flow, so it can be enabled or disabled even after the integration has already been added.

## [2603.090.08] - 2026-03-31

### Features
- **🎉 Major bidirectional sync upgrade** - Alexa Shopping List Sync now delivers a much more complete two-way sync experience between Amazon Alexa and Home Assistant, making the shopping list feel far closer to a truly shared list across both platforms.
- **HA rename sync support** - Added support for item renames from Home Assistant to Alexa by tracking stable Home Assistant item IDs across sync snapshots and converting local name changes into Alexa update operations.

### Improvements
- **HA-style item IDs** - New Home Assistant shopping list items created by the sync now use UUID hex identifiers compatible with Home Assistant's native item format instead of name-derived short hashes.
- **Safer local change detection** - The sync now keeps enough Home Assistant snapshot context to distinguish a real local rename from unrelated bidirectional list changes.
- **Completion sync reliability** - The bidirectional sync flow now behaves much more predictably across add, complete, reopen, and rename scenarios, reducing the chance of items bouncing unexpectedly between Home Assistant and Alexa.


## [2603.090.07] - 2026-03-31

### Bug Fixes
- **Local HA completion precedence** - Fixed a bidirectional sync edge case where an item reopened from Alexa could no longer be completed back to Amazon from Home Assistant. The sync now tracks the last exported HA state so a new local completion wins over automatic remote reopen handling.

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
