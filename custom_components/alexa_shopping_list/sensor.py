#!/usr/bin/env python3

from datetime import timedelta
import logging
import json

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity
)
from homeassistant.components import persistent_notification
from homeassistant.helpers.event import async_track_time_interval

from . import DOMAIN, CONF_SKIP_INITIAL_SYNC, CONF_SYNC_MINS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Setup sensors from a config entry created in the integrations UI."""

    alexa = hass.data[DOMAIN][config_entry.entry_id]

    update_sensor = AlexaShoppingListSyncSensor(hass, alexa, config_entry)

    async_add_entities([update_sensor], update_before_add=True)


class AlexaShoppingListSyncSensor(SensorEntity):
    """Synchronise HA and Alexa shopping lists"""

    def __init__(self, hass, alexa, config_entry):
        self.hass = hass
        self.alexa = alexa
        self.config_entry = config_entry
        self._skip_initial_sync_pending = True

        self._attr_name = "Alexa Shopping List Sync"
        self._attr_icon = "mdi:sync"
        self._attr_unique_id = "alexa_shopping_list_sync"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_should_poll = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        interval = timedelta(minutes=max(1, int(self.config_entry.data.get(CONF_SYNC_MINS, 60))))
        self.async_on_remove(
            async_track_time_interval(self.hass, self._handle_scheduled_update, interval)
        )
        await self._run_sync_update()
        self.async_write_ha_state()

    async def _handle_scheduled_update(self, now) -> None:
        del now
        await self._run_sync_update()
        self.async_write_ha_state()

    async def _run_sync_update(self) -> None:
        try:
            if self._skip_initial_sync_pending:
                self._skip_initial_sync_pending = False
                if self.config_entry.options.get(CONF_SKIP_INITIAL_SYNC, False):
                    _LOGGER.debug("Skipping initial Alexa shopping list sync due to integration option")
                    return

            updated = await self.alexa.sync(_LOGGER)
            if updated == True:
                _LOGGER.debug("Firing alexa_shopping_list_changed event")
                self.hass.bus.async_fire("alexa_shopping_list_changed")
            
            if self.alexa.last_updated is not None:
                self._attr_native_value = self.alexa.last_updated

        except Exception as e:
            _LOGGER.error(f"Alexa Shopping List Sync Error: {e}", exc_info=True)
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
    

    async def async_update(self) -> None:
        await self._run_sync_update()
        self.async_write_ha_state()

