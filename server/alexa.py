#!/usr/bin/env python3

import logging

from browser_backend import BrowserBackend, NotAuthenticatedError

logger = logging.getLogger(__name__)


class AlexaShoppingList:
    def __init__(self, amazon_url: str = "amazon.co.uk", cookies_path: str = ""):
        self.amazon_url = amazon_url
        self.cookies_path = cookies_path
        self._backend = BrowserBackend(amazon_url, cookies_path)

    @property
    def is_authenticated(self):
        return self._backend.is_authenticated

    def close(self):
        self._backend.close()

    # ============================================================
    # Authentication

    def requires_login(self):
        return self._backend.requires_login()

    # ============================================================
    # Alexa lists

    def get_alexa_list(self, refresh: bool = True):
        del refresh
        return self._backend.get_alexa_list()

    def complete_alexa_list_item(self, item: str, alexa_id: str = None):
        return self._backend.complete_alexa_list_item(item, alexa_id=alexa_id)

    def add_alexa_list_item(self, item: str, include_details: bool = False):
        return self._backend.add_alexa_list_item(item, include_details=include_details)

    def update_alexa_list_item(self, old: str, new: str, alexa_id: str = None):
        return self._backend.update_alexa_list_item(old, new, alexa_id=alexa_id)

    def remove_alexa_list_item(self, item: str):
        return self._backend.remove_alexa_list_item(item)

    def bulk_apply_alexa_list_changes(
        self,
        add_items=None,
        remove_items=None,
        update_items=None,
        complete_items=None,
        include_details: bool = False,
    ):
        return self._backend.bulk_apply_alexa_list_changes(
            add_items=add_items,
            remove_items=remove_items,
            update_items=update_items,
            complete_items=complete_items,
            include_details=include_details,
        )
