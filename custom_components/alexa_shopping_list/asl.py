#!/usr/bin/env python3

import websockets
import json
import datetime
import os
import asyncio
import hashlib
import uuid

# ============================================================


class AlexaShoppingListSync:

    COMPLETED_LEDGER_TTL_HOURS = 24

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


    def log_homeassistant_shopping_list_event(self, logger, event):
        if logger is None:
            return

        payload = {
            "event_type": getattr(event, "event_type", None),
            "data": getattr(event, "data", None),
        }
        logger.debug(
            "Received Home Assistant shopping_list_updated event: %s",
            json.dumps(payload, sort_keys=True),
        )

        self._handle_homeassistant_shopping_list_event(logger, payload.get("data") or {})


    def _utcnow(self):
        return datetime.datetime.now(datetime.timezone.utc)


    def _parse_iso_datetime(self, value):
        if not value or not isinstance(value, str):
            return None
        try:
            parsed = datetime.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.astimezone(datetime.timezone.utc)
        except ValueError:
            return None


    def _completed_ledger_cutoff(self):
        return self._utcnow() - datetime.timedelta(hours=self.COMPLETED_LEDGER_TTL_HOURS)


    def _prune_completed_ledger(self, ledger):
        cutoff = self._completed_ledger_cutoff()
        pruned = {}
        for item_id, entry in ledger.items():
            completed_at = self._parse_iso_datetime(entry.get("completed_at"))
            if completed_at is None or completed_at >= cutoff:
                pruned[item_id] = entry
        return pruned


    def _get_completed_ledger(self):
        metadata = self._read_sync_metadata()
        ledger = metadata.get("ha_completed_ledger", {})
        if isinstance(ledger, dict):
            return self._prune_completed_ledger(ledger)
        return {}


    def _set_completed_ledger(self, ledger):
        metadata = self._read_sync_metadata()
        metadata["ha_completed_ledger"] = self._prune_completed_ledger(ledger)
        self._write_sync_metadata(metadata)


    def _update_completed_ledger(self, update_callback):
        ledger = self._get_completed_ledger()
        update_callback(ledger)
        self._set_completed_ledger(ledger)


    def _mark_ledger_item_seen_on_alexa(self, ledger, item_name, alexa_items):
        if item_name not in alexa_items:
            return

        for entry in ledger.values():
            if entry.get("name") == item_name:
                entry["seen_on_alexa"] = True


    def _protected_alexa_items_from_ledger(self, ledger, alexa_items):
        protected = []
        for entry in ledger.values():
            if not entry.get("removed_from_ha"):
                continue
            if not entry.get("seen_on_alexa"):
                continue
            item_name = entry.get("name")
            if item_name and item_name in alexa_items:
                protected.append(item_name)
        return protected


    def _handle_homeassistant_shopping_list_event(self, logger, data):
        action = data.get("action")
        item = data.get("item") or {}
        item_id = item.get("id")
        item_name = item.get("name")
        item_complete = bool(item.get("complete", False))
        previous_alexa_items = self._get_previous_alexa_items()

        def apply_update(ledger):
            if action == "update" and item_id and item_complete:
                ledger[item_id] = {
                    "id": item_id,
                    "name": item_name,
                    "completed_at": self._utcnow().isoformat(),
                    "removed_from_ha": False,
                    "seen_on_alexa": bool(item_name and item_name in previous_alexa_items),
                }
                return

            if action == "update" and item_id and not item_complete:
                ledger.pop(item_id, None)
                return

            if action == "remove" and item_id and item_complete:
                existing = ledger.get(item_id)
                if existing is None:
                    ledger[item_id] = {
                        "id": item_id,
                        "name": item_name,
                        "completed_at": self._utcnow().isoformat(),
                        "removed_from_ha": True,
                        "seen_on_alexa": bool(item_name and item_name in previous_alexa_items),
                    }
                else:
                    existing["name"] = item_name
                    existing["removed_from_ha"] = True
                return

            if action == "clear":
                for entry in ledger.values():
                    entry["removed_from_ha"] = True

        self._update_completed_ledger(apply_update)

        if logger is not None:
            logger.debug(
                "Completed ledger after shopping_list_updated: %s",
                json.dumps(self._get_completed_ledger(), sort_keys=True),
            )
    

    def _build_item_id(self, item_name):
        return uuid.uuid4().hex


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


    def _get_previous_ha_items(self):
        metadata = self._read_sync_metadata()
        items = metadata.get("last_ha_items", [])
        if isinstance(items, list):
            return items
        return []


    def _set_sync_snapshot(self, alexa_items, ha_items):
        metadata = self._read_sync_metadata()
        metadata["last_alexa_items"] = alexa_items
        metadata["last_ha_items"] = ha_items
        self._write_sync_metadata(metadata)


    def _find_ha_list_item(self, find, ha_list):
        for item in ha_list:
            if item['name'] == find:
                return item
        return None


    def _find_ha_list_item_by_id(self, item_id, ha_list):
        for item in ha_list:
            if item.get('id') == item_id:
                return item
        return None


    def _collect_local_updates(self, ha_list, previous_ha_list, alexa_list):
        updates = []

        for item in ha_list:
            item_id = item.get('id')
            if not item_id or item.get('complete') == True:
                continue

            previous_item = self._find_ha_list_item_by_id(item_id, previous_ha_list)
            if previous_item is None:
                continue

            old_name = previous_item.get('name')
            new_name = item.get('name')

            if not old_name or not new_name or old_name == new_name:
                continue

            if previous_item.get('complete') == True:
                continue

            if old_name not in alexa_list:
                continue

            updates.append({
                'old': old_name,
                'new': new_name,
                'id': item_id
            })

        return updates


    def _mark_items_completed(self, ha_list, item_names):
        changed = False
        for item_name in item_names:
            item = self._find_ha_list_item(item_name, ha_list)
            if item is not None and item.get('complete') != True:
                item['complete'] = True
                changed = True
        return changed


    def _mark_items_incomplete(self, ha_list, item_names, previous_ha_list=None):
        changed = False
        previous_ha_list = previous_ha_list or []

        for item_name in item_names:
            item = self._find_ha_list_item(item_name, ha_list)
            if item is None or item.get('complete') != True:
                continue

            previous_item = self._find_ha_list_item(item_name, previous_ha_list)
            if previous_item is not None and previous_item.get('complete') == False:
                # HA changed this item to completed after the last sync; keep that local intent.
                continue

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


    def _strip_names_from_ha_list(self, ha_list, item_names):
        hidden_names = set(item_names)
        return [item for item in ha_list if item.get("name") not in hidden_names]


    def _filter_alexa_items(self, alexa_items, hidden_item_names):
        hidden_names = set(hidden_item_names)
        return [item_name for item_name in alexa_items if item_name not in hidden_names]
    

    async def _debug_log_entry(self, logger=None, entry=""):
        if logger == None:
            return
        logger.debug(entry)


    async def _do_sync(self, loop, logger=None, force=False):

        ha_list = await loop.run_in_executor(None, self._read_ha_shopping_list)
        original_ha_list_hash = await loop.run_in_executor(None, self._ha_shopping_list_hash)
        previous_alexa_list = await loop.run_in_executor(None, self._get_previous_alexa_items)
        previous_ha_list = await loop.run_in_executor(None, self._get_previous_ha_items)
        completed_ledger = await loop.run_in_executor(None, self._get_completed_ledger)
        
        await self._debug_log_entry(logger, "Loading Alexa shopping list")
        alexa_list = await self._get_list(force)
        await self._debug_log_entry(logger, "Alexa list: "+json.dumps(alexa_list))
        await self._debug_log_entry(logger, "Previous Alexa list: "+json.dumps(previous_alexa_list))
        await self._debug_log_entry(logger, "Previous HA list: "+json.dumps(previous_ha_list))
        await self._debug_log_entry(logger, "Completed ledger: "+json.dumps(completed_ledger))

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
            if self._find_ha_list_item(item_name, ha_list) is not None
        ]
        if self._mark_items_incomplete(ha_list, alexa_reopened_in_remote, previous_ha_list):
            await self._debug_log_entry(
                logger,
                "Marked HA items as incomplete from Alexa active list: "+json.dumps(alexa_reopened_in_remote)
            )

        update_items = self._collect_local_updates(ha_list, previous_ha_list, alexa_list)
        updated_new_names = {update['new'] for update in update_items}
        await self._debug_log_entry(logger, "To update on alexa: "+json.dumps(update_items))

        await loop.run_in_executor(None, self._update_completed_ledger, lambda ledger: [
            self._mark_ledger_item_seen_on_alexa(ledger, item_name, previous_alexa_list)
            for item_name in previous_alexa_list
        ])
        completed_ledger = await loop.run_in_executor(None, self._get_completed_ledger)
        await self._debug_log_entry(
            logger,
            "Completed ledger after seen_on_alexa backfill: "+json.dumps(completed_ledger)
        )
        protected_alexa_items = await loop.run_in_executor(
            None, self._protected_alexa_items_from_ledger, completed_ledger, alexa_list
        )
        if protected_alexa_items:
            await self._debug_log_entry(
                logger,
                "Protected Alexa items from HA completed ledger: "+json.dumps(protected_alexa_items)
            )

        to_add = []
        to_complete = []

        for item in ha_list:
            if item['name'] in updated_new_names:
                continue

            if item['complete'] == True:
                if item['name'] in alexa_list:
                    to_complete.append(item['name'])
                continue

            if item['name'] not in alexa_list:
                to_add.append(item['name'])

        for item_name in protected_alexa_items:
            if item_name not in to_complete:
                to_complete.append(item_name)
            if item_name in to_add:
                to_add.remove(item_name)

        filtered_ha_list = await loop.run_in_executor(None, self._strip_names_from_ha_list, ha_list, protected_alexa_items)

        await self._debug_log_entry(logger, "To add to alexa: "+json.dumps(to_add))
        await self._debug_log_entry(logger, "To complete on alexa: "+json.dumps(to_complete))
        if len(to_add) + len(to_complete) + len(update_items) > 1:
            await self._debug_log_entry(logger, "Applying Alexa changes in bulk")
            await self._bulk_apply_changes(add_items=to_add, update_items=update_items, complete_items=to_complete)
        else:
            for update in update_items:
                await self._update_item(update['old'], update['new'])
            for item in to_add:
                await self._add_item(item)
            for item in to_complete:
                await self._complete_item(item)
        
        refreshed_items = await self._get_list()
        await self._debug_log_entry(logger, "Refreshed Alexa list: "+json.dumps(refreshed_items))
        await self._debug_log_entry(logger, "Exporting new HA shopping list")
        await loop.run_in_executor(None, self._update_completed_ledger, lambda ledger: [
            self._mark_ledger_item_seen_on_alexa(ledger, item_name, refreshed_items)
            for item_name in refreshed_items
        ])
        completed_ledger = await loop.run_in_executor(None, self._get_completed_ledger)
        await self._debug_log_entry(
            logger,
            "Completed ledger after refreshed Alexa backfill: "+json.dumps(completed_ledger)
        )
        protected_refreshed_items = await loop.run_in_executor(
            None, self._protected_alexa_items_from_ledger, completed_ledger, refreshed_items
        )
        export_ha_list = await loop.run_in_executor(
            None, self._strip_names_from_ha_list, filtered_ha_list, protected_refreshed_items
        )
        visible_refreshed_items = await loop.run_in_executor(
            None, self._filter_alexa_items, refreshed_items, protected_refreshed_items
        )
        merged_ha_list = await loop.run_in_executor(None, self._merge_ha_with_alexa, export_ha_list, visible_refreshed_items)
        await loop.run_in_executor(None, self._export_ha_shopping_list, merged_ha_list)
        await loop.run_in_executor(None, self._set_sync_snapshot, refreshed_items, merged_ha_list)
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
