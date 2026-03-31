#!/usr/bin/env python3

import websockets
import json
import datetime
import os
import asyncio
import hashlib

# ============================================================


class AlexaShoppingListSync:

    def __init__(self, ip="localhost", port=4000, sync_mins=60, hasl_path=None, hasl_refresh=None):
        self.uri = "ws://"+ip+":"+str(port)
        self._hasl_path = hasl_path
        self._metadata_path = f"{hasl_path}.alexa_sync_meta.json" if hasl_path else None
        self._hasl_refresh = hasl_refresh
        self._setup_cached_list(sync_mins * 60)
        self._sync_lock = asyncio.Lock()
        self.is_authenticated = True

    # ============================================================
    # Helpers


    async def _send_command(self, command, **kwargs):
        async with websockets.connect(self.uri) as websocket:
            request = {
                'command': command,
                'args': {
                    **kwargs
                }
            }
            await websocket.send(json.dumps(request))
            response = await websocket.recv()
            return json.loads(response)
    

    def _command_successful(self, response):
        if "error" in response and response['error'] != None:
            if response['error'] == "Not authenticated":
                self.is_authenticated = False
            return False
        self.is_authenticated = True
        return True
    

    def _command_result(self, response):
        if "result" in response:
            return response['result']
        return None
    

    def _command_error(self, response):
        if "error" in response:
            return response['error']
        return None
        

    # ============================================================
    # Server


    async def can_ping_server(self):
        response = await self._send_command("ping")
        if self._command_successful(response):
            if self._command_result(response) == "pong":
                return True
        return False
    

    async def server_config_is_valid(self):
        response = await self._send_command("config_valid")
        if self._command_successful(response):
            return self._command_result(response)
        return False
    

    async def server_is_authenticated(self):
        response = await self._send_command("authenticated")
        if self._command_successful(response):
            result = self._command_result(response)
            self.is_authenticated = bool(result)
            return self.is_authenticated
        return False
    
    async def get_server_auth_cached_state(self):
        try:
            response = await self._send_command("config_get", key="auth_checked_time")
            if self._command_successful(response):
                result = self._command_result(response)
                self.is_authenticated = bool(result and int(result) > 0)
                return self.is_authenticated
            return False
        except Exception:
            return self.is_authenticated

    # ============================================================
    # Cache


    def _setup_cached_list(self, sync_seconds):
        self._sync_seconds = sync_seconds
        self.last_updated = None
        self._cached_list = []
    

    def _update_cached_list(self, new_list):
        if new_list is None:
            return
        self._cached_list = new_list
        self.last_updated = datetime.datetime.now().astimezone()
    

    def _cached_list_needs_updating(self):
        if self.last_updated == None:
            return True

        now = datetime.datetime.now().astimezone()
        diff = now - self.last_updated

        if diff.total_seconds() >= self._sync_seconds:
            return True
        return False


    # ============================================================
    # Commands


    async def _get_list(self, force=False):
        if self._cached_list_needs_updating() or force:
            response = await self._send_command("get_list")
            if self._command_successful(response):
                self._update_cached_list(self._command_result(response))
            else:
                raise Exception(self._command_error(response))
        return self._cached_list
    

    async def _add_item(self, item):
        response = await self._send_command("add_item", item=item)
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list
    

    async def _update_item(self, old, new):
        response = await self._send_command("update_item", old=old, new=new)
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list
    

    async def _remove_item(self, item):
        response = await self._send_command("remove_item", item=item)
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list


    async def _complete_item(self, item):
        response = await self._send_command("complete_item", item=item)
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list


    async def _bulk_apply_changes(self, add_items=None, remove_items=None, update_items=None, complete_items=None):
        response = await self._send_command(
            "bulk_apply_changes",
            add_items=add_items or [],
            remove_items=remove_items or [],
            update_items=update_items or [],
            complete_items=complete_items or []
        )
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list

    # ============================================================
    # Sync


    async def homeassistant_shopping_list_updated(self, event):
        await self.sync(None, True)
    

    def _build_item_id(self, item_name):
        return hashlib.md5(item_name.encode('utf-8')).hexdigest()[:12]


    def _default_ha_item(self, item_name, complete=False):
        return {
            "id": self._build_item_id(item_name),
            "name": item_name,
            "complete": complete
        }


    def _export_ha_shopping_list(self, items):
        export = []
        for item in items:
            if isinstance(item, dict):
                export.append({
                    "id": item.get("id") or self._build_item_id(item["name"]),
                    "name": item["name"],
                    "complete": bool(item.get("complete", False))
                })
            else:
                export.append(self._default_ha_item(item))
        
        with open(self._hasl_path, "w") as outfile:
            outfile.write(json.dumps(export, indent=4))
    

    def _read_ha_shopping_list(self):
        if os.path.exists(self._hasl_path):
            with open(self._hasl_path, 'r') as file:
                return json.load(file)
        return []
    

    def _ha_shopping_list_hash(self):
        serialized = json.dumps(self._read_ha_shopping_list(), sort_keys=True)
        return hashlib.md5(serialized.encode('utf-8')).hexdigest()
    

    def _read_sync_metadata(self):
        if self._metadata_path and os.path.exists(self._metadata_path):
            with open(self._metadata_path, 'r') as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        return {}


    def _write_sync_metadata(self, metadata):
        if self._metadata_path is None:
            return
        with open(self._metadata_path, "w") as outfile:
            outfile.write(json.dumps(metadata, indent=4, sort_keys=True))


    def _get_previous_alexa_items(self):
        metadata = self._read_sync_metadata()
        items = metadata.get("last_alexa_items", [])
        if isinstance(items, list):
            return items
        return []


    def _set_previous_alexa_items(self, items):
        self._write_sync_metadata({
            "last_alexa_items": items
        })


    def _find_ha_list_item(self, find, ha_list):
        for item in ha_list:
            if item['name'] == find:
                return item
        return None


    def _mark_items_completed(self, ha_list, item_names):
        changed = False
        for item_name in item_names:
            item = self._find_ha_list_item(item_name, ha_list)
            if item is not None and item.get('complete') != True:
                item['complete'] = True
                changed = True
        return changed


    def _mark_items_incomplete(self, ha_list, item_names):
        changed = False
        for item_name in item_names:
            item = self._find_ha_list_item(item_name, ha_list)
            if item is not None and item.get('complete') == True:
                item['complete'] = False
                changed = True
        return changed


    def _merge_ha_with_alexa(self, ha_list, alexa_items):
        merged = []
        seen_names = set()

        for item_name in alexa_items:
            existing = self._find_ha_list_item(item_name, ha_list)
            if existing is None:
                merged.append(self._default_ha_item(item_name, complete=False))
            else:
                merged.append({
                    "id": existing.get("id") or self._build_item_id(item_name),
                    "name": item_name,
                    "complete": False
                })
            seen_names.add(item_name)

        for item in ha_list:
            if item['name'] in seen_names:
                continue
            merged.append({
                "id": item.get("id") or self._build_item_id(item["name"]),
                "name": item["name"],
                "complete": bool(item.get("complete", False))
            })

        return merged
    

    async def _debug_log_entry(self, logger=None, entry=""):
        if logger == None:
            return
        logger.debug(entry)


    async def _do_sync(self, loop, logger=None, force=False):

        ha_list = await loop.run_in_executor(None, self._read_ha_shopping_list)
        original_ha_list_hash = await loop.run_in_executor(None, self._ha_shopping_list_hash)
        previous_alexa_list = await loop.run_in_executor(None, self._get_previous_alexa_items)
        
        await self._debug_log_entry(logger, "Loading Alexa shopping list")
        alexa_list = await self._get_list(force)
        await self._debug_log_entry(logger, "Alexa list: "+json.dumps(alexa_list))
        await self._debug_log_entry(logger, "Previous Alexa list: "+json.dumps(previous_alexa_list))

        if len(previous_alexa_list) > 0:
            alexa_completed_in_remote = [
                item_name for item_name in previous_alexa_list
                if item_name not in alexa_list
            ]
            if self._mark_items_completed(ha_list, alexa_completed_in_remote):
                await self._debug_log_entry(
                    logger,
                    "Marked HA items as completed from Alexa removals: "+json.dumps(alexa_completed_in_remote)
                )

            alexa_reopened_in_remote = [
                item_name for item_name in alexa_list
                if item_name not in previous_alexa_list
            ]
            if self._mark_items_incomplete(ha_list, alexa_reopened_in_remote):
                await self._debug_log_entry(
                    logger,
                    "Marked HA items as incomplete from Alexa re-adds: "+json.dumps(alexa_reopened_in_remote)
                )

        to_add = []
        to_complete = []

        for item in ha_list:
            if item['complete'] == True:
                if item['name'] in alexa_list:
                    to_complete.append(item['name'])
                continue

            if item['name'] not in alexa_list:
                to_add.append(item['name'])

        await self._debug_log_entry(logger, "To add to alexa: "+json.dumps(to_add))
        await self._debug_log_entry(logger, "To complete on alexa: "+json.dumps(to_complete))
        if len(to_add) + len(to_complete) > 1:
            await self._debug_log_entry(logger, "Applying Alexa changes in bulk")
            await self._bulk_apply_changes(add_items=to_add, complete_items=to_complete)
        else:
            for item in to_add:
                await self._add_item(item)
            for item in to_complete:
                await self._complete_item(item)
        
        refreshed_items = await self._get_list()
        await self._debug_log_entry(logger, "Refreshed Alexa list: "+json.dumps(refreshed_items))
        await self._debug_log_entry(logger, "Exporting new HA shopping list")
        merged_ha_list = await loop.run_in_executor(None, self._merge_ha_with_alexa, ha_list, refreshed_items)
        await loop.run_in_executor(None, self._export_ha_shopping_list, merged_ha_list)
        await loop.run_in_executor(None, self._set_previous_alexa_items, refreshed_items)
        await self._hasl_refresh()


        await self._debug_log_entry(logger, "Original list hash: "+original_ha_list_hash)
        new_ha_list_hash = await loop.run_in_executor(None, self._ha_shopping_list_hash)
        await self._debug_log_entry(logger, "New list hash: "+new_ha_list_hash)
        if original_ha_list_hash != new_ha_list_hash:
            await self._debug_log_entry(logger, "List changed")
            return True
        else:
            await self._debug_log_entry(logger, "List did not change")
            return False

    
    async def sync(self, logger=None, force=False):
        loop = asyncio.get_running_loop()

        if os.path.exists(self._hasl_path) == False:
            await self._debug_log_entry(logger, "HA shopping list file not found - creating empty list")
            await loop.run_in_executor(None, self._export_ha_shopping_list, [])

        if self._cached_list_needs_updating() == False and force == False:
            return False
        
        if self._sync_lock.locked():
            await self._debug_log_entry(logger, "Sync already in progress, skipping")
            return False

        result = False
        async with self._sync_lock:
            try:
                result = await self._do_sync(loop, logger, force)
            except Exception as e:
                await self._debug_log_entry(logger, f"Sync error: {type(e).__name__}: {e}")

        return result
    # ============================================================
