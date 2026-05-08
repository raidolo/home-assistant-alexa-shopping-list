#!/usr/bin/env python3

import logging
import json
import asyncio
import websockets

from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components import persistent_notification

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=300)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Setup binary sensors from a config entry created in the integrations UI."""

    alexa = hass.data[DOMAIN][config_entry.entry_id]

    auth_sensor = AlexaShoppingListAuthSensor(hass, alexa)

    async_add_entities([auth_sensor], update_before_add=True)


class AlexaShoppingListAuthSensor(BinarySensorEntity):
    """Binary sensor representing Alexa Shopping List authentication status."""

    def __init__(self, hass, alexa):
        self.hass = hass
        self.alexa = alexa

        self._attr_name = "Alexa Shopping List Authenticated"
        self._attr_unique_id = "alexa_shopping_list_authenticated"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

        self._failed_auth_checks = 0
        self._required_failed_checks = 2

    @property
    def is_on(self):
        """Return true if the server is authenticated."""

        return self.alexa.is_authenticated

    async def async_update(self) -> None:
        """Fetch authentication status using the websocket authenticated command."""

        auth_ok = False

        try:
            async with websockets.connect(
                self.alexa.uri,
                open_timeout=5,
                close_timeout=2
            ) as websocket:
                await websocket.send(json.dumps({
                    "command": "authenticated",
                    "args": {}
                }))

                response = await asyncio.wait_for(websocket.recv(), timeout=5)
                data = json.loads(response)

                auth_ok = (
                    data.get("result") is True
                    and data.get("error") is None
                )

        except Exception as e:
            _LOGGER.debug("Alexa Shopping List authentication check failed: %s", e)
            auth_ok = False

        if auth_ok:
            self._failed_auth_checks = 0
            self.alexa.is_authenticated = True

            persistent_notification.async_dismiss(
                self.hass,
                "alexa_shopping_list_auth"
            )

            return

        self._failed_auth_checks += 1

        if self._failed_auth_checks >= self._required_failed_checks:
            self.alexa.is_authenticated = False

            persistent_notification.async_create(
                self.hass,
                "Alexa Shopping List requires re-authentication. Please open the addon Web UI and log in again.",
                title="Alexa Shopping List Auth Expired",
                notification_id="alexa_shopping_list_auth"
            )

        else:
            _LOGGER.debug(
                "Alexa Shopping List authentication check failed %s/%s; waiting before notifying",
                self._failed_auth_checks,
                self._required_failed_checks
            )
