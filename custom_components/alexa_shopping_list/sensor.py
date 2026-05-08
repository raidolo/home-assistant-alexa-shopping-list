#!/usr/bin/env python3

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity
)

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Setup sensors from a config entry created in the integrations UI."""

    alexa = hass.data[DOMAIN][config_entry.entry_id]

    update_sensor = AlexaShoppingListSyncSensor(hass, alexa)

    async_add_entities([update_sensor], update_before_add=False)


class AlexaShoppingListSyncSensor(SensorEntity):
    """Passive timestamp sensor for last Alexa Shopping List sync."""

    def __init__(self, hass, alexa):
        self.hass = hass
        self.alexa = alexa

        self._attr_name = "Alexa Shopping List Sync"
        self._attr_icon = "mdi:sync"
        self._attr_unique_id = "alexa_shopping_list_sync"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_should_poll = False

        if self.alexa.last_updated is not None:
            self._attr_native_value = self.alexa.last_updated

    async def async_added_to_hass(self) -> None:
        """Update timestamp when a sync completes successfully."""

        async def _handle_sync_event(event):
            if self.alexa.last_updated is not None:
                self._attr_native_value = self.alexa.last_updated
                self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen(
                "alexa_shopping_list_changed",
                _handle_sync_event
            )
        )

    async def async_update(self) -> None:
        """Refresh timestamp only from memory. Do not trigger sync here."""

        if self.alexa.last_updated is not None:
            self._attr_native_value = self.alexa.last_updated
