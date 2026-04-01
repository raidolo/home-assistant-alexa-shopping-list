#!/usr/bin/env python3

import logging

from .asl import AlexaShoppingListSync
from homeassistant.components import persistent_notification

_LOGGER = logging.getLogger(__name__)

DOMAIN = "alexa_shopping_list"

CONF_IP = "server_ip"
CONF_PORT = "server_port"
CONF_SYNC_MINS = "sync_mins"
CONF_SKIP_INITIAL_SYNC = "skip_initial_sync"

SERVICE_SYNC = "sync_alexa_shopping_list"


async def async_setup_entry(hass, entry):
    """Set up platform from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})

    try:

        alexa = AlexaShoppingListSync(
            entry.data[CONF_IP],
            entry.data[CONF_PORT],
            entry.data[CONF_SYNC_MINS],
            hass.config.path(".shopping_list.json"),
            hass.data["shopping_list"].async_load
        )

    except Exception as e:
        _LOGGER.error(f"Error during async_setup_entry: {e}", exc_info=True)
        return False
    
    # hass.bus.async_listen("shopping_list_updated", alexa.homeassistant_shopping_list_updated)
    alexa._shopping_list_event_unsub = hass.bus.async_listen(
        "shopping_list_updated",
        lambda event: hass.async_create_task(
            alexa.log_homeassistant_shopping_list_event(_LOGGER, event)
        ),
    )
    hass.data[DOMAIN][entry.entry_id] = alexa
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "binary_sensor"])

    services = AlexaServices(alexa, _LOGGER, hass)
    hass.services.async_register(DOMAIN, SERVICE_SYNC, services.handle_sync_service)

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    alexa = hass.data[DOMAIN].pop(entry.entry_id, None)
    if alexa is not None:
        unsub = getattr(alexa, "_shopping_list_event_unsub", None)
        if unsub is not None:
            unsub()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "binary_sensor"])
    return unload_ok


class AlexaServices:

    def __init__(self, alexa, logger, hass):
        self.alexa = alexa
        self.logger = logger
        self.hass = hass

    async def handle_sync_service(self, call):
        self.logger.info("Alexa Shopping List manual sync triggered via Home Assistant action")

        try:
            updated = await self.alexa.sync(self.logger, True)
            if updated == True:
                _LOGGER.debug("Firing alexa_shopping_list_changed event")
                self.hass.bus.async_fire("alexa_shopping_list_changed")
        except Exception as e:
            self.logger.error(f"Alexa Shopping List Sync Error: {e}", exc_info=True)
        finally:
            if self.alexa.is_authenticated:
                persistent_notification.async_dismiss(self.hass, "alexa_shopping_list_auth")
            else:
                persistent_notification.async_create(
                    self.hass,
                    "Alexa Shopping List requires re-authentication. Please open the addon Web UI and log in again.",
                    title="Alexa Shopping List Auth Expired",
                    notification_id="alexa_shopping_list_auth"
                )

