# Sync to v2605.128.00 Implementation Plan

## Goal

Bring the local repository into sync with `raidolo/home-assistant-alexa-shopping-list`
tag `v2605.128.00` while preserving the local sync improvements and fixing the
Alexa-side delete bug documented in `Sync issue 2026-05-22.md`.

The local branch is divergent, not simply behind. Do not reset local history to
the upstream tag, because local contains functional sync/server improvements that
are not present upstream.

## Current State

Upstream target:

- Repository: `https://github.com/raidolo/home-assistant-alexa-shopping-list`
- Tag: `v2605.128.00`
- Commit: `3036c1194c3fd338bdf95948735c626877a8edce`

Local target:

- Branch: `main`
- Commit: `762afe9394edf5f0c4986bf383febedc91ed8ac6`

Known divergence:

- Upstream-only commits:
  - `47ea952` Add shopping list loader compatibility for HA 2026.5
  - `34b271d` Bump version to 2605.128.00
  - `3036c11` Add Git line ending rules
- Local-only commits:
  - `056d9b7` Guard against partial Alexa list refresh after sync
  - `0009776` Track last synced list to propagate HA deletions to Alexa
  - `055cf41` Improve server error guidance for Amazon challenges
  - `e2e5ba9` Handle Alexa-side completed shopping list items
  - `50ffa87` Use Alexa item IDs for shopping list sync
  - `05ea9a3` Fetch Alexa list items inside browser session
  - `e22d1a9` Fix shopping list runtime lookup on newer Home Assistant
  - `762afe9` Merge branch `guard-against-partial-refresh`

## Strategy

Use a merge branch, not a reset.

```powershell
git remote add upstream https://github.com/raidolo/home-assistant-alexa-shopping-list.git
git fetch upstream --tags
git switch -c sync-v2605.128.00
git merge v2605.128.00
```

If `upstream` already exists, skip `git remote add upstream`.

## Merge Resolution Policy

Keep upstream metadata changes:

- Add upstream `.gitattributes`.
- Update `custom_components/alexa_shopping_list/manifest.json` to
  `2605.128.00`.
- Add the `2605.128.00` changelog entry.

Preserve local behavioral changes:

- Keep ID-aware Alexa item fetching in `server/alexa.py`.
- Keep the `get_list_items` command path in `server/server.py`.
- Keep friendly server error messages for Amazon sign-in, CAPTCHA, MFA, WAF, and
  non-JSON responses.
- Keep local sync-state tracking in
  `custom_components/alexa_shopping_list/asl.py`.
- Keep the partial-refresh guard that refuses to export an obviously truncated
  post-mutation Alexa list back into Home Assistant.
- Keep the local Home Assistant shopping-list runtime-data loader in
  `custom_components/alexa_shopping_list/__init__.py`.

Conflict note:

Upstream commit `47ea952` and local commit `e22d1a9` both address Home Assistant
2026.5 shopping-list compatibility. Prefer the local runtime-data implementation
because it checks the loaded `shopping_list` integration and its config-entry
`runtime_data`, then refreshes and notifies the runtime data object directly.
Upstream's `_get_shopping_data(hass).async_load` fallback should be treated as
the older/smaller fix unless local testing proves otherwise.

## Required Bug Fix

Bug source: `Sync issue 2026-05-22.md`.

Current incorrect behavior:

When an item is active in Home Assistant but missing from the current Alexa
active list, sync can add it back to Alexa even if the item existed in the last
sync snapshot. That incorrectly restores items intentionally deleted from Alexa.

Desired behavior:

When `last_synced_active_items` exists, if an item is:

- active in Home Assistant,
- missing from the current Alexa active list,
- and present in `last_synced_active_items`,

then treat it as an Alexa-side delete. Do not add it back to Alexa. Log the
event as `Detected Alexa-side deletes`. These items should disappear from Home
Assistant when the refreshed Alexa list is exported.

Only infer Alexa-side deletes when a previous snapshot exists. If there is no
snapshot, preserve current bootstrap behavior: active Home Assistant-only items
should be added to Alexa.

Do not add Alexa-side deletes to `to_remove`, because they are already gone from
Alexa.

## Bug Fix Design

Patch `custom_components/alexa_shopping_list/asl.py` in `_do_sync`.

Build snapshot lookup structures before iterating Home Assistant items:

- `last_synced_by_ha_id`: already exists and should remain.
- `last_synced_by_id`: already exists and should remain.
- Add a name-based fallback lookup for legacy/name-only snapshot records, for
  example `last_synced_names = {item["name"] ...}`.

During the active Home Assistant item loop:

1. Keep the existing completed-item handling.
2. Resolve `alexa_id` using `_ha_item_alexa_id(item)`.
3. If the item has an Alexa ID but no current Alexa active item, treat it as an
   Alexa-side delete and append the item name to `alexa_removed_names`.
4. If the item has no resolved Alexa ID, is missing from `alexa_active_names`,
   and a previous snapshot exists:
   - If its Home Assistant ID or name is present in the snapshot, append it to
     `alexa_removed_names`.
   - Do not append it to `to_add`.
5. If there is no previous snapshot, or the active HA-only item was not present
   in the previous snapshot, append it to `to_add`.

Rename or adjust the debug log from:

```text
Previously synced items missing from Alexa; treating as Alexa-side completed/removed:
```

to:

```text
Detected Alexa-side deletes:
```

Keep the existing HA-delete inference loop separate:

- Snapshotted item deleted from Home Assistant and still present in Alexa should
  still be added to `to_remove`.
- Alexa-side deletes must not be added to `to_remove`.

## Validation Plan

Static checks:

```powershell
python -m compileall custom_components server client
```

Manual or automated sync-case checks:

1. New HA item not in snapshot and missing from Alexa:
   - Expected: added to Alexa.
2. Snapshotted item active in HA but deleted from Alexa:
   - Expected: logged as `Detected Alexa-side deletes`.
   - Expected: not added to Alexa.
   - Expected: removed from HA after refreshed Alexa list export.
3. Snapshotted item deleted from HA but present in Alexa:
   - Expected: added to `to_remove` and removed from Alexa.
4. New Alexa item not in HA and not in snapshot:
   - Expected: imported to HA after refreshed Alexa list export.

Regression checks:

- First sync with no previous `.alexa_shopping_list_sync_state.json` still adds
  active HA-only items to Alexa.
- ID-aware server fetch still works through `get_list_items`.
- If an ID-aware snapshot exists but the server falls back to name-only reads,
  sync still refuses to export fallback data.
- Partial-refresh guard still prevents exporting a truncated Alexa list after
  mutations.
- Home Assistant 2026.5 shopping-list refresh still succeeds with the local
  runtime-data loader.

## Implementation Sequence

1. Create merge branch `sync-v2605.128.00`.
2. Merge upstream tag `v2605.128.00`.
3. Resolve conflicts according to this plan.
4. Implement the Alexa-side delete bug fix in `asl.py`.
5. Update or add focused tests if this repo has a test harness available. If no
   harness exists, document the manual test matrix from this plan in the final
   verification notes.
6. Run `python -m compileall custom_components server client`.
7. Review the final diff to confirm only intended files changed.
8. Commit the sync and bug fix together or as two commits:
   - `Merge upstream v2605.128.00 while preserving local sync fixes`
   - `Handle snapshotted Alexa-side deletes symmetrically`

## Acceptance Criteria

- Local code reports version `2605.128.00`.
- Upstream `.gitattributes` is present.
- Upstream changelog entry for `2605.128.00` is present.
- Local ID-aware sync/server improvements remain intact.
- Local Home Assistant 2026.5 runtime-data loader remains intact.
- Snapshotted Alexa-side deletes are not re-added to Alexa.
- Snapshotted Home Assistant-side deletes are still removed from Alexa.
- New Home Assistant-only items are still added to Alexa when no previous
  snapshot exists or when they were not in the previous snapshot.
- Static compilation passes.
