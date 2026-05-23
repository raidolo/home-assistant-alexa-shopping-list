Patch custom_components/alexa_shopping_list/asl.py to use the last sync snapshot symmetrically.

Current bug:
If an active HA item is missing from the current Alexa list, the sync always adds it back to Alexa. This incorrectly restores items that were intentionally deleted from Alexa.

Desired logic:
When last_synced_active_items exists, if an item:
- is active in HA,
- is missing from the current Alexa list,
- and existed in last_synced_active_items,

then treat it as an Alexa-side delete. Do not add it back to Alexa. Log it as "Detected Alexa-side deletes". These items should disappear from HA when the refreshed Alexa list is exported.

Only infer Alexa-side deletes when a previous snapshot exists. If no snapshot exists, preserve current behavior: active HA-only items should be added to Alexa.

Do not add Alexa-side deletes to to_remove because they are already gone from Alexa.

Test cases:
1. New HA item not in snapshot and missing from Alexa -> added to Alexa.
2. Snapshotted item active in HA but deleted from Alexa -> logged as Alexa-side delete and not re-added.
3. Snapshotted item deleted from HA but present in Alexa -> removed from Alexa.
4. New Alexa item not in HA and not in snapshot -> imported to HA.