# Development Workflow

## Dev Release Workflow

When a fix is ready or tested, follow the full dev release workflow unless explicitly told otherwise:

1. Bump the custom component version in `custom_components/alexa_shopping_list/manifest.json`.
2. Update `CHANGELOG.md` in this repository.
3. Sync the dev changelog in the `home-assistant-alexa-shopping-list-hass` repository.
4. Commit the relevant repository changes.
5. Push the corresponding branches.

This is the default release flow for fixes and tested changes.
