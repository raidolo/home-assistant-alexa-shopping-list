# Development Workflow

## Dev Release Workflow

When a fix is ready or tested, follow the full dev release workflow unless explicitly told otherwise:

1. Bump the custom component version in `custom_components/alexa_shopping_list/manifest.json`.
2. Update `CHANGELOG.md` in this repository.
3. Sync the dev changelog in the `home-assistant-alexa-shopping-list-hass` repository.
4. Commit the relevant repository changes.
5. Push the corresponding branches.

This is the default release flow for fixes and tested changes.

## TODO

- Handle the edge case where completed items are deleted from Home Assistant before the next sync.
  Current behavior: if a completed item is removed from HA before sync, the integration can no longer recognize it as a previously completed item and may import it back from Alexa as if it were a new addition.
  Planned fix: revisit this flow during the Home Assistant primitive migration, so add, delete, complete, reopen, and update operations are handled through HA-native shopping list/todo primitives instead of relying on manual file reconstruction and snapshot inference.
