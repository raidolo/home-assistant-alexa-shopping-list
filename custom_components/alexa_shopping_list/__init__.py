#!/usr/bin/env python3

import logging
import asyncio
from datetime import timedelta

from .asl import AlexaShoppingListSync
from homeassistant.helpers.event import async_track_time_interval, async_call_later
from homeassistant.components.shopping_list.common import _get_shopping_data

_LOGGER = logging.getLogger(__name__)

DOMAIN = "alexa_shopping_list"

CONF_IP = "server_ip"
CONF_PORT = "server_port"
CONF_SYNC_MINS = "sync_mins"

SERVICE_SYNC = "sync_alexa_shopping_list"


async def async_setup_entry(hass, entry):
    """Set up platform from a ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})

    async def _shopping_list_refresh():
        try:
            await _get_shopping_data(hass).async_load()
        except Exception as e:
            _LOGGER.debug("Shopping list refresh skipped: %s", e)

    try:

        alexa = AlexaShoppingListSync(
            entry.data[CONF_IP],
            entry.data[CONF_PORT],
            entry.data[CONF_SYNC_MINS],
            hass.config.path(".shopping_list.json"),
            _shopping_list_refresh
        )

    except Exception as e:
        _LOGGER.error(f"Error during async_setup_entry: {e}", exc_info=True)
        return False
    
    # hass.bus.async_listen("shopping_list_updated", alexa.homeassistant_shopping_list_updated)
    hass.data[DOMAIN][entry.entry_id] = alexa
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "binary_sensor"])

    services = AlexaServices(alexa, _LOGGER, hass)
    hass.services.async_register(DOMAIN, SERVICE_SYNC, services.handle_sync_service)

    async def _scheduled_sync(now):
        _LOGGER.info("Alexa Shopping List scheduled sync triggered by custom component")

        try:
            before_last_updated = alexa.last_updated

            await alexa.sync(_LOGGER, True)

            if alexa.last_updated is not None and alexa.last_updated != before_last_updated:
                _LOGGER.debug("Firing alexa_shopping_list_changed event")
                hass.bus.async_fire("alexa_shopping_list_changed")

        except Exception as e:
            _LOGGER.error(f"Alexa Shopping List Scheduled Sync Error: {e}", exc_info=True)

    remove_scheduled_sync = async_track_time_interval(
        hass,
        _scheduled_sync,
        timedelta(minutes=entry.data[CONF_SYNC_MINS])
    )

    entry.async_on_unload(remove_scheduled_sync)

    async def _startup_sync(now):
        _LOGGER.debug("Alexa Shopping List startup sync check started")

        max_attempts = 10
        retry_delay_seconds = 30

        for attempt in range(1, max_attempts + 1):
            try:
                server_ready = await alexa.can_ping_server()

                if server_ready:
                    _LOGGER.info("Alexa Shopping List server ready, running startup sync")

                    before_last_updated = alexa.last_updated

                    await alexa.sync(_LOGGER, True)

                    if alexa.last_updated is not None and alexa.last_updated != before_last_updated:
                        _LOGGER.debug("Firing alexa_shopping_list_changed event")
                        hass.bus.async_fire("alexa_shopping_list_changed")

                    return

                _LOGGER.debug(
                    "Alexa Shopping List server not ready, startup sync attempt %s/%s",
                    attempt,
                    max_attempts
                )

            except Exception as e:
                _LOGGER.debug(
                    "Alexa Shopping List startup sync attempt %s/%s failed: %s",
                    attempt,
                    max_attempts,
                    e
                )

            await asyncio.sleep(retry_delay_seconds)

        _LOGGER.warning("Alexa Shopping List startup sync skipped: server not ready after retries")


    remove_startup_sync = async_call_later(hass, 60, _startup_sync)
    entry.async_on_unload(remove_startup_sync)

    return True


class AlexaServices:

    def __init__(self, alexa, logger, hass):
        self.alexa = alexa
        self.logger = logger
        self.hass = hass

    async def handle_sync_service(self, call):
        self.logger.info("Alexa Shopping List manual sync triggered via Home Assistant action")

        try:
            before_last_updated = self.alexa.last_updated

            await self.alexa.sync(self.logger, True)

            if self.alexa.last_updated is not None and self.alexa.last_updated != before_last_updated:
                _LOGGER.debug("Firing alexa_shopping_list_changed event")
                self.hass.bus.async_fire("alexa_shopping_list_changed")

        except Exception as e:
            self.logger.error(f"Alexa Shopping List Sync Error: {e}", exc_info=True)
