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
        self._cached_items = []
    

    def _update_cached_list(self, new_list):
        if new_list is None:
            return
        self._cached_list = new_list
        self.last_updated = datetime.datetime.now().astimezone()


    def _update_cached_items(self, new_items):
        if new_items is None:
            return
        self._cached_items = new_items
        self._cached_list = [
            self._item_name(item)
            for item in new_items
            if self._item_name(item) is not None and not self._item_complete(item)
        ]
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


    async def _get_list(self, force = False):
        if self._cached_list_needs_updating() or force:
            response = await self._send_command("get_list")
            if self._command_successful(response):
                self._update_cached_list(self._command_result(response))
            else:
                raise Exception(self._command_error(response))
        return self._cached_list


    async def _get_items(self, force = False):
        if self._cached_list_needs_updating() or force or not self._cached_items:
            response = await self._send_command("get_list_items")
            if self._command_successful(response):
                self._update_cached_items(self._command_result(response))
            else:
                # Older servers do not expose ID-aware reads. Fall back to the
                # existing name-only scrape so sync remains backward compatible.
                names = await self._get_list(force)
                self._update_cached_items([
                    {"id": None, "list_id": None, "name": name, "complete": False}
                    for name in names
                ])
        return self._cached_items
    

    async def _add_item(self, item):
        response = await self._send_command("add_item", item=item)
        if self._command_successful(response):
            self.last_updated = None
        else:
            raise Exception(self._command_error(response))
        return self._cached_list
    

    async def _update_item(self, old, new):
        response = await self._send_command("update_item", old=old, new=new)
        if self._command_successful(response):
            self.last_updated = None
        else:
            raise Exception(self._command_error(response))
        return self._cached_list
    

    async def _remove_item(self, item):
        response = await self._send_command("remove_item", item=item)
        if self._command_successful(response):
            self.last_updated = None
        else:
            raise Exception(self._command_error(response))
        return self._cached_list


    async def _bulk_apply_changes(self, add_items=None, remove_items=None, update_items=None):
        response = await self._send_command(
            "bulk_apply_changes",
            add_items=add_items or [],
            remove_items=remove_items or [],
            update_items=update_items or []
        )
        if self._command_successful(response):
            self.last_updated = None
        else:
            raise Exception(self._command_error(response))
        return self._cached_list

    # ============================================================
    # Sync


    def _item_name(self, item):
        if isinstance(item, dict):
            return item.get("name")
        return item


    def _item_id(self, item):
        if isinstance(item, dict):
            return item.get("id")
        return None


    def _item_list_id(self, item):
        if isinstance(item, dict):
            return item.get("list_id")
        return None


    def _item_complete(self, item):
        if isinstance(item, dict):
            return bool(item.get("complete"))
        return False


    def _item_ha_id(self, item):
        name = self._item_name(item)
        item_id = self._item_id(item)
        if item_id:
            return item_id
        return hashlib.md5(name.encode('utf-8')).hexdigest()[:12]


    def _item_state_record(self, item):
        name = self._item_name(item)
        if not name:
            return None

        return {
            "id": self._item_id(item),
            "ha_id": self._item_ha_id(item),
            "list_id": self._item_list_id(item),
            "name": name,
        }


    async def homeassistant_shopping_list_updated(self, event):
        await self.sync(None, True)
    

    def _export_ha_shopping_list(self, items):
        export = []
        for item in items:
            name = self._item_name(item)
            if not name:
                continue

            export.append({
                "id": self._item_ha_id(item),
                "name": name,
                "complete": False
            })
        
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
    

    def _sync_state_path(self):
        return os.path.join(
            os.path.dirname(self._hasl_path),
            ".alexa_shopping_list_sync_state.json"
        )


    def _read_last_synced_active_items(self):
        path = self._sync_state_path()
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            tracked_items = data.get("last_synced_items")
            if isinstance(tracked_items, list):
                return [
                    item
                    for item in tracked_items
                    if isinstance(item, dict) and item.get("name")
                ]

            items = data.get("last_synced_active_items")
            if isinstance(items, list):
                return [
                    {"id": None, "ha_id": hashlib.md5(item.encode('utf-8')).hexdigest()[:12], "list_id": None, "name": item}
                    for item in items
                ]
        except Exception:
            pass

        return None


    def _write_last_synced_active_items(self, items):
        path = self._sync_state_path()
        tmp_path = path + ".tmp"
        records = [
            record
            for record in (self._item_state_record(item) for item in items)
            if record is not None
        ]
        data = {
            "last_synced_items": sorted(records, key=lambda item: (item["name"].casefold(), item["ha_id"])),
            "last_synced_active_items": sorted(
                set(record["name"] for record in records),
                key=str.casefold
            ),
            "updated_at": datetime.datetime.now().astimezone().isoformat(),
        }

        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

        os.replace(tmp_path, path)
    

    def _find_ha_list_item(self, find, ha_list):
        for item in ha_list:
            if item['name'] == find:
                return item
        return None
    

    async def _debug_log_entry(self, logger=None, entry=""):
        if logger == None:
            return
        logger.debug(entry)


    async def _do_sync(self, loop, logger=None, force=False):

        ha_list = await loop.run_in_executor(None, self._read_ha_shopping_list)
        original_ha_list_hash = await loop.run_in_executor(None, self._ha_shopping_list_hash)
        
        await self._debug_log_entry(logger, "Loading Alexa shopping list")
        alexa_items = await self._get_items(force)
        alexa_active_items = [
            item
            for item in alexa_items
            if not self._item_complete(item)
        ]
        alexa_list = [
            self._item_name(item)
            for item in alexa_active_items
            if self._item_name(item) is not None
        ]
        await self._debug_log_entry(logger, "Alexa list: "+json.dumps(alexa_list))

        last_synced_active_items = await loop.run_in_executor(
            None,
            self._read_last_synced_active_items
        )

        has_last_synced_snapshot = last_synced_active_items is not None
        last_synced_active_items = last_synced_active_items or []
        alexa_active_by_id = {
            self._item_id(item): item
            for item in alexa_active_items
            if self._item_id(item)
        }
        id_aware_fetch = not alexa_items or any(self._item_id(item) for item in alexa_items)
        alexa_active_names = set(alexa_list)
        last_synced_by_ha_id = {
            item.get("ha_id"): item
            for item in last_synced_active_items
            if isinstance(item, dict) and item.get("ha_id")
        }
        last_synced_by_id = {
            item.get("id"): item
            for item in last_synced_active_items
            if isinstance(item, dict) and item.get("id")
        }
        last_synced_names = {
            item.get("name")
            for item in last_synced_active_items
            if isinstance(item, dict) and item.get("name")
        }

        id_snapshot_name_fallback = False
        if last_synced_by_id and not id_aware_fetch:
            id_snapshot_name_fallback = True
            await self._debug_log_entry(
                logger,
                "Alexa ID snapshot exists but current fetch did not include IDs; falling back to name-based comparison"
            )
            alexa_active_by_id = {}
            last_synced_by_id = {}
            last_synced_by_ha_id = {}

        def _ha_item_alexa_id(item):
            ha_id = item.get("id")
            if ha_id in alexa_active_by_id or ha_id in last_synced_by_id:
                return ha_id

            previous = last_synced_by_ha_id.get(ha_id)
            if previous is not None:
                return previous.get("id")

            return None

        def _ha_item_in_last_synced_snapshot(item):
            ha_id = item.get("id")
            if ha_id and ha_id in last_synced_by_ha_id:
                return True
            if ha_id and ha_id in last_synced_by_id:
                return True
            return item.get("name") in last_synced_names

        current_ha_ids = set()
        for item in ha_list:
            ha_id = item.get("id")
            if ha_id:
                current_ha_ids.add(ha_id)

        alexa_removed_names = []
        to_add = []
        to_remove = []
        to_update = []

        for item in ha_list:
            name = item["name"]
            alexa_id = _ha_item_alexa_id(item)

            if item.get("complete") == True:
                if alexa_id in alexa_active_by_id:
                    to_remove.append(self._item_name(alexa_active_by_id[alexa_id]))
                elif name in alexa_active_names:
                    to_remove.append(name)
                continue

            if alexa_id:
                alexa_item = alexa_active_by_id.get(alexa_id)
                if alexa_item is None:
                    alexa_removed_names.append(name)
                    continue

                alexa_name = self._item_name(alexa_item)
                if alexa_name != name:
                    to_update.append({
                        "old": alexa_name,
                        "new": name
                    })
            elif name not in alexa_active_names:
                if has_last_synced_snapshot and _ha_item_in_last_synced_snapshot(item):
                    alexa_removed_names.append(name)
                else:
                    to_add.append(name)

        if has_last_synced_snapshot:
            for item in last_synced_active_items:
                name = item.get("name")
                alexa_id = item.get("id")
                ha_id = item.get("ha_id")

                if ha_id in current_ha_ids:
                    continue

                if alexa_id and alexa_id in alexa_active_by_id:
                    to_remove.append(self._item_name(alexa_active_by_id[alexa_id]))
                elif name in alexa_active_names:
                    to_remove.append(name)
        else:
            await self._debug_log_entry(
                logger,
                "No previous sync snapshot found; not inferring HA deletes on this run"
            )

        to_add = sorted(set(to_add), key=str.casefold)
        to_remove = sorted(set(to_remove), key=str.casefold)
        to_update = sorted(
            {json.dumps(update, sort_keys=True) for update in to_update},
            key=str.casefold
        )
        to_update = [json.loads(update) for update in to_update]
        alexa_removed_names = sorted(set(alexa_removed_names), key=str.casefold)

        await self._debug_log_entry(logger, "To add to alexa: "+json.dumps(to_add))
        await self._debug_log_entry(logger, "To remove from alexa: "+json.dumps(to_remove))
        await self._debug_log_entry(logger, "To update in alexa: "+json.dumps(to_update))
        if alexa_removed_names:
            await self._debug_log_entry(
                logger,
                "Detected Alexa-side deletes: " + json.dumps(alexa_removed_names)
            )
        mutation_count = len(to_add) + len(to_remove) + len(to_update)
        if mutation_count > 1:
            await self._debug_log_entry(logger, "Applying Alexa changes in bulk")
            await self._bulk_apply_changes(
                add_items=to_add,
                remove_items=to_remove,
                update_items=to_update
            )
        else:
            for item in to_add:
                await self._add_item(item)
            for item in to_remove:
                await self._remove_item(item)
            for update in to_update:
                await self._update_item(update["old"], update["new"])
        
        refreshed_items = await self._get_items(force=bool(mutation_count))
        refreshed_active_items = [
            item
            for item in refreshed_items
            if not self._item_complete(item)
        ]
        refreshed_list = [
            self._item_name(item)
            for item in refreshed_active_items
            if self._item_name(item) is not None
        ]
        await self._debug_log_entry(logger, "Refreshed Alexa list: "+json.dumps(refreshed_list))

        # Defensive guard: after adding/removing items, do not allow an obviously
        # truncated post-mutation scrape to overwrite Home Assistant's local list.
        if mutation_count and len(refreshed_active_items) < len(alexa_active_items) - len(to_remove):
            await self._debug_log_entry(
                logger,
                "Refreshed Alexa list looks truncated; refusing to export to HA. "
                f"before={len(alexa_active_items)} "
                f"refreshed={len(refreshed_active_items)} "
                f"to_add={json.dumps(to_add)} "
                f"to_remove={json.dumps(to_remove)} "
                f"to_update={json.dumps(to_update)}"
            )
            return False

        await self._debug_log_entry(logger, "Exporting new HA shopping list")
        await loop.run_in_executor(None, self._export_ha_shopping_list, refreshed_active_items)
        await loop.run_in_executor(None, self._write_last_synced_active_items, refreshed_active_items)
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

