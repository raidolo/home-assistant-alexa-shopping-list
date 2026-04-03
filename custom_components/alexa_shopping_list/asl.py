#!/usr/bin/env python3

import websockets
import json
import datetime
import os
import asyncio
import hashlib
import uuid
from collections import Counter, defaultdict

# ============================================================


class AlexaShoppingListSync:

    COMPLETED_LEDGER_TTL_HOURS = 24

    def __init__(self, ip="localhost", port=4000, sync_mins=60, hasl_path=None, hasl_refresh=None, hass=None, ha_entity_id="todo.shopping_list"):
        self.uri = "ws://"+ip+":"+str(port)
        self._hasl_path = hasl_path
        self._metadata_path = f"{hasl_path}.alexa_sync_meta.json" if hasl_path else None
        self._hasl_refresh = hasl_refresh
        self._hass = hass
        self._ha_entity_id = ha_entity_id
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
            result = self._command_result(response)
            if isinstance(result, dict) and "list" in result:
                self._update_cached_list(result.get("list"))
            else:
                self._update_cached_list(result)
        return self._cached_list
    

    async def _update_item(self, old, new, alexa_id=None):
        response = await self._send_command("update_item", old=old, new=new, alexa_id=alexa_id)
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list
    

    async def _remove_item(self, item):
        response = await self._send_command("remove_item", item=item)
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list


    async def _complete_item(self, item, alexa_id=None):
        response = await self._send_command("complete_item", item=item, alexa_id=alexa_id)
        if self._command_successful(response):
            self._update_cached_list(self._command_result(response))
        return self._cached_list


    async def _bulk_apply_changes(self, add_items=None, remove_items=None, update_items=None, complete_items=None, include_details=False):
        response = await self._send_command(
            "bulk_apply_changes",
            add_items=add_items or [],
            remove_items=remove_items or [],
            update_items=update_items or [],
            complete_items=complete_items or [],
            include_details=include_details
        )
        if self._command_successful(response):
            result = self._command_result(response)
            if isinstance(result, dict) and "list" in result:
                self._update_cached_list(result.get("list"))
                return result
            self._update_cached_list(result)
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


    def _clear_ledger_for_active_ha_items(self, ledger, ha_list):
        active_names = {item.get("name") for item in ha_list if item.get("complete") != True}
        for item_id in list(ledger.keys()):
            if ledger[item_id].get("name") in active_names:
                del ledger[item_id]


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

        return
    

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


    def _ha_items_hash(self, items):
        serialized = json.dumps(items, sort_keys=True)
        return hashlib.md5(serialized.encode('utf-8')).hexdigest()


    def _ha_items_signature(self, items):
        signature = []
        for item in items:
            if not isinstance(item, dict):
                continue
            signature.append((item.get("name"), bool(item.get("complete", False))))
        signature.sort()
        return signature


    async def _todo_get_items(self, statuses=None):
        if self._hass is None:
            return None

        response = await self._hass.services.async_call(
            "todo",
            "get_items",
            {
                "status": statuses or ["needs_action", "completed"],
            },
            blocking=True,
            target={"entity_id": self._ha_entity_id},
            return_response=True,
        )
        return response


    def _normalize_todo_items_response(self, response):
        if not isinstance(response, dict):
            return []

        entity_result = response.get(self._ha_entity_id, {})
        items = entity_result.get("items", [])
        if not isinstance(items, list):
            return []

        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("uid")
            item_name = item.get("summary")
            if not item_id or not item_name:
                continue
            normalized.append({
                "id": item_id,
                "name": item_name,
                "complete": item.get("status") == "completed",
            })
        return normalized


    async def _read_ha_shopping_list_async(self):
        if self._hass is None:
            return self._read_ha_shopping_list()

        response = await self._todo_get_items()
        return self._normalize_todo_items_response(response)


    async def _ha_add_item(self, item_name):
        await self._hass.services.async_call(
            "todo",
            "add_item",
            {"item": item_name},
            blocking=True,
            target={"entity_id": self._ha_entity_id},
        )


    async def _ha_update_item(self, item_id, rename=None, complete=None):
        service_data = {"item": item_id}
        if rename is not None:
            service_data["rename"] = rename
        if complete is not None:
            service_data["status"] = "completed" if complete else "needs_action"

        await self._hass.services.async_call(
            "todo",
            "update_item",
            service_data,
            blocking=True,
            target={"entity_id": self._ha_entity_id},
        )


    async def _ha_remove_item(self, item_id):
        await self._hass.services.async_call(
            "todo",
            "remove_item",
            {"item": item_id},
            blocking=True,
            target={"entity_id": self._ha_entity_id},
        )


    async def _apply_ha_shopping_list(self, desired_items):
        if self._hass is None:
            self._export_ha_shopping_list(desired_items)
            return self._read_ha_shopping_list()

        current_items = await self._read_ha_shopping_list_async()
        current_by_id = {item["id"]: item for item in current_items}
        desired_existing_ids = set()
        additions = []

        for desired_item in desired_items:
            desired_id = desired_item.get("id")
            desired_name = desired_item["name"]
            desired_complete = bool(desired_item.get("complete", False))

            if desired_id and desired_id in current_by_id:
                desired_existing_ids.add(desired_id)
                current_item = current_by_id[desired_id]

                rename = desired_name if current_item.get("name") != desired_name else None
                complete = desired_complete if bool(current_item.get("complete", False)) != desired_complete else None
                if rename is not None or complete is not None:
                    await self._ha_update_item(desired_id, rename=rename, complete=complete)
            else:
                additions.append(desired_item)

        for current_item in current_items:
            if current_item["id"] in desired_existing_ids:
                continue
            await self._ha_remove_item(current_item["id"])

        for desired_item in additions:
            await self._ha_add_item(desired_item["name"])
        desired_signature = self._ha_items_signature(desired_items)
        latest_items = await self._read_ha_shopping_list_async()
        if self._ha_items_signature(latest_items) == desired_signature:
            return latest_items

        for _ in range(10):
            await asyncio.sleep(0.2)
            latest_items = await self._read_ha_shopping_list_async()
            if self._ha_items_signature(latest_items) == desired_signature:
                return latest_items

        return latest_items
    

    def _read_sync_metadata(self):
        if self._metadata_path and os.path.exists(self._metadata_path):
            try:
                with open(self._metadata_path, 'r') as file:
                    data = json.load(file)
                    if isinstance(data, dict):
                        return data
            except (json.JSONDecodeError, OSError):
                return {}
        return {}


    def _write_sync_metadata(self, metadata):
        if self._metadata_path is None:
            return
        tmp_metadata_path = f"{self._metadata_path}.{uuid.uuid4().hex}.tmp"
        with open(tmp_metadata_path, "w") as outfile:
            outfile.write(json.dumps(metadata, indent=4, sort_keys=True))
        os.replace(tmp_metadata_path, self._metadata_path)


    def _get_previous_alexa_items(self):
        return self._active_alexa_item_names(self._get_previous_alexa_snapshot())


    def _get_previous_alexa_snapshot(self):
        metadata = self._read_sync_metadata()
        items = metadata.get("last_alexa_items", [])
        return self._normalize_alexa_items(items)


    def _get_previous_ha_items(self):
        metadata = self._read_sync_metadata()
        items = metadata.get("last_ha_items", [])
        if isinstance(items, list):
            return items
        return []


    def _set_sync_snapshot(self, alexa_items, ha_items):
        metadata = self._read_sync_metadata()
        metadata["last_alexa_items"] = self._normalize_alexa_items(alexa_items)
        metadata["last_ha_items"] = ha_items
        self._write_sync_metadata(metadata)


    def _get_item_links(self):
        metadata = self._read_sync_metadata()
        links = metadata.get("item_links", {})
        if isinstance(links, dict):
            return links
        return {}


    def _set_item_links(self, links):
        metadata = self._read_sync_metadata()
        metadata["item_links"] = links
        self._write_sync_metadata(metadata)


    def _normalize_alexa_items(self, items):
        normalized = []
        if not isinstance(items, list):
            return normalized

        for item in items:
            if isinstance(item, str):
                normalized.append({
                    "id": None,
                    "name": item,
                    "complete": False,
                    "createdDateTime": None,
                    "updatedDateTime": None,
                    "version": None,
                    "listId": None,
                    "defaultList": True,
                })
                continue

            if not isinstance(item, dict):
                continue

            item_name = item.get("name")
            if not item_name:
                continue

            normalized.append({
                "id": item.get("id"),
                "name": item_name,
                "complete": bool(item.get("complete", False)),
                "createdDateTime": item.get("createdDateTime"),
                "updatedDateTime": item.get("updatedDateTime"),
                "version": item.get("version"),
                "listId": item.get("listId"),
                "defaultList": bool(item.get("defaultList", True)),
            })

        return normalized


    def _active_alexa_item_names(self, items):
        return [
            item["name"]
            for item in self._normalize_alexa_items(items)
            if bool(item.get("complete", False)) is False
        ]


    def _find_alexa_item_by_id(self, item_id, alexa_items):
        for item in self._normalize_alexa_items(alexa_items):
            if item.get("id") == item_id:
                return item
        return None


    def _link_ha_and_alexa_items(self, links, ha_item_id, alexa_item_id):
        if not ha_item_id or not alexa_item_id:
            return

        stale_ha_ids = [existing_ha_id for existing_ha_id, existing_alexa_id in links.items() if existing_alexa_id == alexa_item_id and existing_ha_id != ha_item_id]
        for stale_ha_id in stale_ha_ids:
            del links[stale_ha_id]

        links[ha_item_id] = alexa_item_id


    def _prune_item_links(self, links, ha_items, alexa_items):
        ha_items_by_id = {
            item.get("id"): item
            for item in ha_items
            if item.get("id")
        }
        alexa_items_by_id = {
            item.get("id"): item
            for item in self._normalize_alexa_items(alexa_items)
            if item.get("id")
        }

        return {
            ha_item_id: alexa_item_id
            for ha_item_id, alexa_item_id in links.items()
            if ha_item_id in ha_items_by_id and alexa_item_id in alexa_items_by_id
        }


    def _bootstrap_item_links(self, links, ha_items, alexa_items):
        normalized_alexa = self._normalize_alexa_items(alexa_items)
        linked_alexa_ids = set(links.values())

        active_ha_by_name = defaultdict(list)
        for item in ha_items:
            if bool(item.get("complete", False)):
                continue
            item_id = item.get("id")
            if not item_id or item_id in links:
                continue
            active_ha_by_name[item["name"]].append(item)

        active_alexa_by_name = defaultdict(list)
        for item in normalized_alexa:
            if bool(item.get("complete", False)):
                continue
            item_id = item.get("id")
            if not item_id or item_id in linked_alexa_ids:
                continue
            active_alexa_by_name[item["name"]].append(item)

        for item_name, ha_candidates in active_ha_by_name.items():
            alexa_candidates = active_alexa_by_name.get(item_name, [])
            pair_count = min(len(ha_candidates), len(alexa_candidates))
            for index in range(pair_count):
                self._link_ha_and_alexa_items(
                    links,
                    ha_candidates[index].get("id"),
                    alexa_candidates[index].get("id"),
                )

        return links


    def _apply_remote_linked_changes(self, ha_list, previous_ha_list, previous_alexa_items, current_alexa_items, links):
        changed = False

        for ha_item in ha_list:
            ha_item_id = ha_item.get("id")
            if not ha_item_id:
                continue

            alexa_item_id = links.get(ha_item_id)
            if not alexa_item_id:
                continue

            current_alexa_item = self._find_alexa_item_by_id(alexa_item_id, current_alexa_items)
            previous_alexa_item = self._find_alexa_item_by_id(alexa_item_id, previous_alexa_items)
            previous_ha_item = self._find_ha_list_item_by_id(ha_item_id, previous_ha_list)

            if current_alexa_item is None or previous_alexa_item is None or previous_ha_item is None:
                continue

            if (
                previous_alexa_item.get("name") != current_alexa_item.get("name")
                and ha_item.get("name") == previous_ha_item.get("name") == previous_alexa_item.get("name")
            ):
                ha_item["name"] = current_alexa_item.get("name")
                changed = True

            if (
                bool(previous_alexa_item.get("complete", False)) is False
                and bool(current_alexa_item.get("complete", False)) is True
                and bool(previous_ha_item.get("complete", False)) is False
                and bool(ha_item.get("complete", False)) is False
            ):
                ha_item["complete"] = True
                changed = True

        return changed


    def _remote_linked_missing_or_completed_items(self, ha_list, previous_ha_list, previous_alexa_items, current_alexa_items, links):
        completed_item_names = []

        for ha_item in ha_list:
            ha_item_id = ha_item.get("id")
            if not ha_item_id:
                continue

            alexa_item_id = links.get(ha_item_id)
            if not alexa_item_id:
                continue

            previous_alexa_item = self._find_alexa_item_by_id(alexa_item_id, previous_alexa_items)
            current_alexa_item = self._find_alexa_item_by_id(alexa_item_id, current_alexa_items)
            previous_ha_item = self._find_ha_list_item_by_id(ha_item_id, previous_ha_list)

            if previous_alexa_item is None or previous_ha_item is None:
                continue

            if bool(previous_alexa_item.get("complete", False)):
                continue

            if bool(previous_ha_item.get("complete", False)) or bool(ha_item.get("complete", False)):
                continue

            if current_alexa_item is None or bool(current_alexa_item.get("complete", False)):
                completed_item_names.append(ha_item.get("name"))

        return completed_item_names


    def _collect_linked_local_completions(self, ha_list, previous_ha_list, current_alexa_items, links):
        completions = []

        for ha_item in ha_list:
            ha_item_id = ha_item.get("id")
            if not ha_item_id or bool(ha_item.get("complete", False)) is False:
                continue

            alexa_item_id = links.get(ha_item_id)
            if not alexa_item_id:
                continue

            previous_ha_item = self._find_ha_list_item_by_id(ha_item_id, previous_ha_list)
            if previous_ha_item is None or bool(previous_ha_item.get("complete", False)):
                continue

            current_alexa_item = self._find_alexa_item_by_id(alexa_item_id, current_alexa_items)
            if current_alexa_item is None or bool(current_alexa_item.get("complete", False)):
                continue

            completions.append({
                "name": ha_item.get("name"),
                "alexa_id": alexa_item_id,
            })

        return completions


    def _filter_unlinked_alexa_active_names(self, alexa_items, links, ha_items=None):
        links = links or {}
        ha_items_by_id = {
            item.get("id"): item
            for item in (ha_items or [])
            if item.get("id")
        }
        filtered = []
        for item in self._normalize_alexa_items(alexa_items):
            if bool(item.get("complete", False)):
                continue
            item_id = item.get("id")
            if item_id in links.values():
                linked_ha_item_id = next(
                    (ha_item_id for ha_item_id, alexa_item_id in links.items() if alexa_item_id == item_id),
                    None,
                )
                linked_ha_item = ha_items_by_id.get(linked_ha_item_id)
                if (
                    linked_ha_item is not None
                    and bool(linked_ha_item.get("complete", False)) is False
                    and linked_ha_item.get("name") == item.get("name")
                ):
                    continue
            filtered.append(item["name"])
        return filtered


    def _filter_unlinked_ha_items(self, ha_items, links, alexa_items=None):
        filtered = []
        links = links or {}
        alexa_items_by_id = {
            item.get("id"): item
            for item in self._normalize_alexa_items(alexa_items or [])
            if item.get("id")
        }
        for item in ha_items:
            item_id = item.get("id")
            if item_id and item_id in links:
                linked_alexa_item = alexa_items_by_id.get(links[item_id])
                if (
                    bool(item.get("complete", False)) is False
                    and
                    linked_alexa_item is not None
                    and bool(linked_alexa_item.get("complete", False)) is False
                    and linked_alexa_item.get("name") == item.get("name")
                ):
                    continue
            filtered.append(item)
        return filtered


    def _compact_alexa_snapshot_for_log(self, items):
        compact = []
        for item in self._normalize_alexa_items(items):
            compact.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "complete": bool(item.get("complete", False)),
                "createdDateTime": item.get("createdDateTime"),
                "updatedDateTime": item.get("updatedDateTime"),
            })
        return compact


    def _dedupe_ha_items_by_id(self, ha_items):
        deduped = []
        seen_ids = set()

        for item in ha_items:
            item_id = item.get("id")
            if item_id:
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
            deduped.append(item)

        return deduped


    def _compact_links_for_log(self, links, ha_items=None, alexa_items=None, extra_alexa_items=None):
        ha_items_by_id = {
            item.get("id"): item
            for item in (ha_items or [])
            if item.get("id")
        }
        alexa_items_by_id = {
            item.get("id"): item
            for item in self._normalize_alexa_items((alexa_items or []) + (extra_alexa_items or []))
            if item.get("id")
        }

        compact = []
        for ha_item_id, alexa_item_id in sorted((links or {}).items()):
            ha_item = ha_items_by_id.get(ha_item_id, {})
            alexa_item = alexa_items_by_id.get(alexa_item_id, {})
            compact.append({
                "ha_id": ha_item_id,
                "ha_name": ha_item.get("name"),
                "ha_complete": bool(ha_item.get("complete", False)),
                "alexa_id": alexa_item_id,
                "alexa_name": alexa_item.get("name"),
                "alexa_complete": bool(alexa_item.get("complete", False)),
            })
        return compact


    def _remap_links_to_applied_ha_ids(self, links, desired_ha_items, applied_ha_items):
        links = links or {}
        if not links:
            return {}

        desired_by_id = {
            item.get("id"): item
            for item in desired_ha_items
            if item.get("id")
        }
        applied_ids = {
            item.get("id")
            for item in applied_ha_items
            if item.get("id")
        }

        remapped = {}
        used_applied_ids = set()

        for ha_item_id, alexa_item_id in links.items():
            if ha_item_id in applied_ids:
                remapped[ha_item_id] = alexa_item_id
                used_applied_ids.add(ha_item_id)

        available_applied_by_signature = defaultdict(list)
        for item in applied_ha_items:
            item_id = item.get("id")
            if not item_id or item_id in used_applied_ids:
                continue
            signature = (item.get("name"), bool(item.get("complete", False)))
            available_applied_by_signature[signature].append(item_id)

        for desired_item in desired_ha_items:
            desired_id = desired_item.get("id")
            if not desired_id or desired_id in remapped:
                continue
            if desired_id not in links:
                continue

            signature = (desired_item.get("name"), bool(desired_item.get("complete", False)))
            candidates = available_applied_by_signature.get(signature, [])
            if not candidates:
                continue

            applied_id = candidates.pop(0)
            remapped[applied_id] = links[desired_id]
            used_applied_ids.add(applied_id)

        return remapped


    def _link_added_alexa_items(self, links, ha_items, added_alexa_items):
        normalized_added = self._normalize_alexa_items(added_alexa_items)
        if not normalized_added:
            return links

        linked_ha_ids = set(links.keys())

        available_ha_by_name = defaultdict(list)
        for item in ha_items:
            if bool(item.get("complete", False)):
                continue
            item_id = item.get("id")
            if not item_id or item_id in linked_ha_ids:
                continue
            available_ha_by_name[item.get("name")].append(item_id)

        for alexa_item in normalized_added:
            alexa_id = alexa_item.get("id")
            alexa_name = alexa_item.get("name")
            if not alexa_id or not alexa_name:
                continue
            if not available_ha_by_name[alexa_name]:
                continue
            ha_item_id = available_ha_by_name[alexa_name].pop(0)
            self._link_ha_and_alexa_items(links, ha_item_id, alexa_id)

        return links


    def _find_ha_list_item(self, find, ha_list):
        for item in ha_list:
            if item['name'] == find:
                return item
        return None


    def _find_ha_list_items(self, find, ha_list, complete=None):
        items = []
        for item in ha_list:
            if item.get('name') != find:
                continue
            if complete is not None and bool(item.get('complete', False)) != complete:
                continue
            items.append(item)
        return items


    def _find_ha_list_item_by_id(self, item_id, ha_list):
        for item in ha_list:
            if item.get('id') == item_id:
                return item
        return None


    def _collect_local_updates(self, ha_list, previous_ha_list, alexa_list, item_links=None):
        updates = []
        item_links = item_links or {}

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

            if item_links.get(item_id) is None and old_name not in alexa_list:
                continue

            updates.append({
                'old': old_name,
                'new': new_name,
                'id': item_id,
                'alexa_id': item_links.get(item_id)
            })

        return updates


    def _collect_local_completions(self, ha_list, previous_ha_list):
        previous_completed_counts = Counter()
        current_completed_counts = Counter()

        for item in previous_ha_list:
            if item.get("complete") == True:
                previous_completed_counts[item["name"]] += 1

        for item in ha_list:
            if item.get("complete") == True:
                current_completed_counts[item["name"]] += 1

        completed_counts = Counter()
        for item_name, current_count in current_completed_counts.items():
            increased_count = max(current_count - previous_completed_counts[item_name], 0)
            if increased_count > 0:
                completed_counts[item_name] = increased_count

        return completed_counts


    def _collect_local_open_count_drops(self, ha_list, previous_ha_list):
        previous_open_counts = Counter()
        current_open_counts = Counter()

        for item in previous_ha_list:
            if item.get("complete") == True:
                continue
            previous_open_counts[item["name"]] += 1

        for item in ha_list:
            if item.get("complete") == True:
                continue
            current_open_counts[item["name"]] += 1

        dropped_counts = Counter()
        for item_name, previous_count in previous_open_counts.items():
            dropped_count = max(previous_count - current_open_counts[item_name], 0)
            if dropped_count > 0:
                dropped_counts[item_name] = dropped_count

        return dropped_counts


    def _mark_items_completed(self, ha_list, item_names):
        changed = False
        for item_name, count in Counter(item_names).items():
            for item in self._find_ha_list_items(item_name, ha_list, complete=False)[:count]:
                item['complete'] = True
                changed = True
        return changed


    def _mark_items_incomplete(self, ha_list, item_names, previous_ha_list=None):
        changed = False
        previous_ha_list = previous_ha_list or []
        local_completed_counts = Counter()

        for item in ha_list:
            if item.get("complete") != True:
                continue
            previous_item = self._find_ha_list_item_by_id(item.get("id"), previous_ha_list)
            if previous_item is not None and previous_item.get("complete") == False:
                local_completed_counts[item["name"]] += 1

        for item_name, count in Counter(item_names).items():
            previous_open_count = len(self._find_ha_list_items(item_name, previous_ha_list, complete=False))
            reopen_budget = max(count - previous_open_count, 0)
            if reopen_budget <= 0:
                continue

            completed_items = self._find_ha_list_items(item_name, ha_list, complete=True)
            protected_local_completions = min(local_completed_counts[item_name], len(completed_items))
            reopen_candidates = completed_items[protected_local_completions:]

            for item in reopen_candidates[:reopen_budget]:
                item['complete'] = False
                changed = True
        return changed


    def _merge_ha_with_alexa(self, ha_list, alexa_items, protected_completed_counts=None):
        merged = []
        remaining_active_by_name = defaultdict(list)
        remaining_completed_by_name = defaultdict(list)
        protected_completed_counts = Counter(protected_completed_counts or {})

        for item in ha_list:
            if bool(item.get("complete", False)):
                remaining_completed_by_name[item["name"]].append(item)
            else:
                remaining_active_by_name[item["name"]].append(item)

        for item_name in alexa_items:
            existing_items = remaining_active_by_name.get(item_name, [])
            if len(existing_items) == 0:
                # Only suppress remote re-add for one sync when the same item
                # was just completed locally in Home Assistant.
                if protected_completed_counts[item_name] > 0:
                    protected_completed_counts[item_name] -= 1
                    continue
                merged.append(self._default_ha_item(item_name, complete=False))
            else:
                existing = existing_items.pop(0)
                merged.append({
                    "id": existing.get("id") or self._build_item_id(item_name),
                    "name": item_name,
                    "complete": False
                })

        for remaining_items in remaining_active_by_name.values():
            for item in remaining_items:
                merged.append({
                    "id": item.get("id") or self._build_item_id(item["name"]),
                    "name": item["name"],
                    "complete": False
                })

        for remaining_items in remaining_completed_by_name.values():
            for item in remaining_items:
                merged.append({
                    "id": item.get("id") or self._build_item_id(item["name"]),
                    "name": item["name"],
                    "complete": True
                })

        return merged


    def _strip_names_from_ha_list(self, ha_list, item_names):
        hidden_counts = Counter(item_names)
        stripped = []
        for item in ha_list:
            item_name = item.get("name")
            if hidden_counts[item_name] > 0:
                hidden_counts[item_name] -= 1
                continue
            stripped.append(item)
        return stripped


    def _filter_alexa_items(self, alexa_items, hidden_item_names):
        hidden_counts = Counter(hidden_item_names)
        filtered = []
        for item_name in alexa_items:
            if hidden_counts[item_name] > 0:
                hidden_counts[item_name] -= 1
                continue
            filtered.append(item_name)
        return filtered


    def _mark_items_completed_from_count_delta(self, ha_list, before_items, after_items, ignored_removed_counts=None):
        changed = False
        before_counts = Counter(before_items)
        after_counts = Counter(after_items)
        ignored_removed_counts = ignored_removed_counts or Counter()

        for item_name, before_count in before_counts.items():
            removed_count = max(before_count - after_counts[item_name], 0)
            if ignored_removed_counts[item_name] > 0:
                removed_count = max(removed_count - ignored_removed_counts[item_name], 0)
            if removed_count <= 0:
                continue

            for item in self._find_ha_list_items(item_name, ha_list, complete=False)[:removed_count]:
                item["complete"] = True
                changed = True

        return changed
    

    async def _debug_log_entry(self, logger=None, entry=""):
        if logger == None:
            return
        logger.debug(entry)

    async def _sync_phase_load_state(self, loop, logger=None, force=False):
        ha_list = await self._read_ha_shopping_list_async()
        original_ha_list_hash = self._ha_items_hash(ha_list)
        previous_alexa_snapshot = await loop.run_in_executor(None, self._get_previous_alexa_snapshot)
        previous_ha_list = await loop.run_in_executor(None, self._get_previous_ha_items)
        completed_ledger = await loop.run_in_executor(None, self._get_completed_ledger)
        item_links = await loop.run_in_executor(None, self._get_item_links)

        await self._debug_log_entry(logger, "Loading Alexa shopping list")
        alexa_snapshot = self._normalize_alexa_items(await self._get_list(force))
        alexa_list = self._active_alexa_item_names(alexa_snapshot)
        await self._debug_log_entry(logger, "Alexa list: " + json.dumps(alexa_list))

        return {
            "ha_list": ha_list,
            "original_ha_list_hash": original_ha_list_hash,
            "previous_alexa_snapshot": previous_alexa_snapshot,
            "previous_alexa_list": self._active_alexa_item_names(previous_alexa_snapshot),
            "previous_ha_list": previous_ha_list,
            "completed_ledger": completed_ledger,
            "item_links": item_links,
            "previous_item_links": dict(item_links),
            "alexa_snapshot": alexa_snapshot,
            "alexa_list": alexa_list,
        }

    async def _sync_phase_prepare_links(self, loop, state, logger=None):
        state["item_links"] = await loop.run_in_executor(
            None,
            self._prune_item_links,
            state["item_links"],
            state["ha_list"],
            state["alexa_snapshot"],
        )
        if state["item_links"] != state["previous_item_links"]:
            await self._debug_log_entry(
                logger,
                "Item links after prune: "
                + json.dumps(
                    self._compact_links_for_log(
                        state["item_links"], state["ha_list"], state["alexa_snapshot"]
                    )
                ),
            )

        state["previous_item_links"] = dict(state["item_links"])
        state["item_links"] = await loop.run_in_executor(
            None,
            self._bootstrap_item_links,
            state["item_links"],
            state["ha_list"],
            state["alexa_snapshot"],
        )
        if state["item_links"] != state["previous_item_links"]:
            await self._debug_log_entry(
                logger,
                "Item links after bootstrap: "
                + json.dumps(
                    self._compact_links_for_log(
                        state["item_links"], state["ha_list"], state["alexa_snapshot"]
                    )
                ),
            )

        state["previous_item_links"] = dict(state["item_links"])
        return state

    async def _sync_phase_apply_remote_changes(self, loop, state, logger=None):
        if await loop.run_in_executor(
            None,
            self._apply_remote_linked_changes,
            state["ha_list"],
            state["previous_ha_list"],
            state["previous_alexa_snapshot"],
            state["alexa_snapshot"],
            state["item_links"],
        ):
            await self._debug_log_entry(logger, "Applied linked remote Alexa changes to HA")

        linked_remote_completed_names = await loop.run_in_executor(
            None,
            self._remote_linked_missing_or_completed_items,
            state["ha_list"],
            state["previous_ha_list"],
            state["previous_alexa_snapshot"],
            state["alexa_snapshot"],
            state["item_links"],
        )
        state["linked_remote_completed_names"] = linked_remote_completed_names
        if linked_remote_completed_names and self._mark_items_completed(state["ha_list"], linked_remote_completed_names):
            await self._debug_log_entry(
                logger,
                "Marked linked HA items as completed from Alexa id changes: "
                + json.dumps(linked_remote_completed_names),
            )

        return state

    async def _sync_phase_collect_unlinked_views(self, loop, state):
        state["unlinked_previous_alexa_list"] = await loop.run_in_executor(
            None,
            self._filter_unlinked_alexa_active_names,
            state["previous_alexa_snapshot"],
            state["item_links"],
            state["previous_ha_list"],
        )
        state["unlinked_alexa_list"] = await loop.run_in_executor(
            None,
            self._filter_unlinked_alexa_active_names,
            state["alexa_snapshot"],
            state["item_links"],
            state["ha_list"],
        )
        state["unlinked_ha_list"] = await loop.run_in_executor(
            None,
            self._filter_unlinked_ha_items,
            state["ha_list"],
            state["item_links"],
            state["alexa_snapshot"],
        )
        state["unlinked_previous_ha_list"] = await loop.run_in_executor(
            None,
            self._filter_unlinked_ha_items,
            state["previous_ha_list"],
            state["item_links"],
            state["previous_alexa_snapshot"],
        )
        state["previous_alexa_counts"] = Counter(state["unlinked_previous_alexa_list"])
        state["current_alexa_counts"] = Counter(state["unlinked_alexa_list"])
        return state

    async def _sync_phase_plan_alexa_changes(self, loop, state, logger=None):
        if len(state["unlinked_previous_alexa_list"]) > 0:
            local_open_count_drops = self._collect_local_open_count_drops(
                state["unlinked_ha_list"], state["unlinked_previous_ha_list"]
            )
            local_complete_ha_counts = self._collect_local_completions(
                state["unlinked_ha_list"], state["unlinked_previous_ha_list"]
            )
            alexa_completed_in_remote = []
            for item_name, previous_count in state["previous_alexa_counts"].items():
                removed_count = max(previous_count - state["current_alexa_counts"][item_name], 0)
                local_drop_count = local_open_count_drops[item_name]
                local_completed_count = local_complete_ha_counts[item_name]
                effective_removed_count = max(
                    removed_count - local_drop_count - local_completed_count, 0
                )
                if (local_drop_count > 0 or local_completed_count > 0) and effective_removed_count != removed_count:
                    await self._debug_log_entry(
                        logger,
                        "Filtered Alexa removals by local HA changes for "
                        + item_name
                        + ": "
                        + json.dumps(
                            {
                                "remote_removed": removed_count,
                                "local_open_drop": local_drop_count,
                                "local_completed_increase": local_completed_count,
                                "effective_remote_removed": effective_removed_count,
                            }
                        ),
                    )
                alexa_completed_in_remote.extend([item_name] * effective_removed_count)

            if self._mark_items_completed(state["unlinked_ha_list"], alexa_completed_in_remote):
                await self._debug_log_entry(
                    logger,
                    "Marked HA items as completed from Alexa removals: "
                    + json.dumps(alexa_completed_in_remote),
                )
        else:
            alexa_completed_in_remote = []

        state["alexa_completed_in_remote"] = alexa_completed_in_remote
        state["alexa_reopened_in_remote"] = []
        state["update_items"] = self._collect_local_updates(
            state["ha_list"],
            state["previous_ha_list"],
            state["alexa_list"],
            item_links=state["item_links"],
        )
        state["updated_new_names"] = {update["new"] for update in state["update_items"]}
        await self._debug_log_entry(logger, "To update on alexa: " + json.dumps(state["update_items"]))

        state["linked_complete_items"] = await loop.run_in_executor(
            None,
            self._collect_linked_local_completions,
            state["ha_list"],
            state["previous_ha_list"],
            state["alexa_snapshot"],
            state["item_links"],
        )
        state["linked_complete_counts"] = Counter(
            item.get("name")
            for item in state["linked_complete_items"]
            if isinstance(item, dict) and item.get("name")
        )
        await loop.run_in_executor(
            None,
            self._update_completed_ledger,
            lambda ledger: self._clear_ledger_for_active_ha_items(ledger, state["ha_list"]),
        )
        await loop.run_in_executor(
            None,
            self._update_completed_ledger,
            lambda ledger: [
                self._mark_ledger_item_seen_on_alexa(ledger, item_name, state["previous_alexa_list"])
                for item_name in state["previous_alexa_list"]
            ],
        )
        state["completed_ledger"] = await loop.run_in_executor(None, self._get_completed_ledger)
        state["protected_alexa_items"] = []

        to_add = []
        to_complete = list(state["linked_complete_items"])
        alexa_counts = Counter(state["unlinked_alexa_list"])
        open_ha_counts = Counter()
        state["local_complete_ha_counts"] = self._collect_local_completions(
            state["unlinked_ha_list"], state["unlinked_previous_ha_list"]
        )

        for item in state["unlinked_ha_list"]:
            if item["name"] in state["updated_new_names"]:
                continue
            if item["complete"] != True:
                open_ha_counts[item["name"]] += 1

        for item_name, count in open_ha_counts.items():
            missing_count = max(count - alexa_counts[item_name], 0)
            to_add.extend([item_name] * missing_count)

        for item_name, alexa_count in alexa_counts.items():
            excess_remote_count = max(alexa_count - open_ha_counts[item_name], 0)
            local_completion_budget = max(
                state["local_complete_ha_counts"][item_name] - state["linked_complete_counts"][item_name],
                0,
            )
            completable_count = min(local_completion_budget, excess_remote_count)
            to_complete.extend([item_name] * completable_count)

        previous_unlinked_open_ha_counts = Counter()
        current_unlinked_completed_ha_counts = Counter()
        for item in state["unlinked_previous_ha_list"]:
            if bool(item.get("complete", False)) is False:
                previous_unlinked_open_ha_counts[item["name"]] += 1
        for item in state["unlinked_ha_list"]:
            if bool(item.get("complete", False)):
                current_unlinked_completed_ha_counts[item["name"]] += 1

        for item_name, alexa_count in alexa_counts.items():
            if open_ha_counts[item_name] > 0:
                continue
            if previous_unlinked_open_ha_counts[item_name] > 0:
                continue
            if current_unlinked_completed_ha_counts[item_name] <= 0:
                continue

            persistent_remote_count = min(state["previous_alexa_counts"][item_name], alexa_count)
            already_scheduled = sum(
                1
                for scheduled in to_complete
                if not isinstance(scheduled, dict) and scheduled == item_name
            )
            carryover_count = max(persistent_remote_count - already_scheduled, 0)
            if carryover_count > 0:
                to_complete.extend([item_name] * carryover_count)

        remote_completed_counts = Counter(alexa_completed_in_remote)
        if remote_completed_counts:
            filtered_to_complete = []
            for item_name in to_complete:
                if isinstance(item_name, dict):
                    filtered_to_complete.append(item_name)
                    continue
                if remote_completed_counts[item_name] > 0:
                    remote_completed_counts[item_name] -= 1
                    continue
                filtered_to_complete.append(item_name)
            to_complete = filtered_to_complete

        for item_name in state["protected_alexa_items"]:
            to_complete.append(item_name)
            if item_name in to_add:
                to_add.remove(item_name)

        state["filtered_ha_list"] = await loop.run_in_executor(
            None,
            self._strip_names_from_ha_list,
            state["ha_list"],
            state["protected_alexa_items"],
        )
        state["to_add"] = to_add
        state["to_complete"] = to_complete

        await self._debug_log_entry(logger, "To add to alexa: " + json.dumps(to_add))
        await self._debug_log_entry(logger, "To complete on alexa: " + json.dumps(to_complete))
        return state

    async def _sync_phase_apply_alexa_changes(self, loop, state, logger=None):
        added_alexa_items = []
        if len(state["to_add"]) + len(state["to_complete"]) + len(state["update_items"]) > 1:
            await self._debug_log_entry(logger, "Applying Alexa changes in bulk")
            bulk_result = await self._bulk_apply_changes(
                add_items=state["to_add"],
                update_items=state["update_items"],
                complete_items=state["to_complete"],
                include_details=True,
            )
            if isinstance(bulk_result, dict):
                added_alexa_items = bulk_result.get("added_items", [])
        else:
            for update in state["update_items"]:
                await self._update_item(update["old"], update["new"], alexa_id=update.get("alexa_id"))
            for item in state["to_add"]:
                add_response = await self._send_command("add_item", item=item, include_details=True)
                if self._command_successful(add_response):
                    add_result = self._command_result(add_response)
                    if isinstance(add_result, dict) and "list" in add_result:
                        self._update_cached_list(add_result.get("list"))
                        added_alexa_items.extend(add_result.get("added_items", []))
                    else:
                        self._update_cached_list(add_result)
            for item in state["to_complete"]:
                if isinstance(item, dict):
                    await self._complete_item(item.get("name") or "", alexa_id=item.get("alexa_id"))
                else:
                    await self._complete_item(item)

        state["added_alexa_items"] = added_alexa_items
        state["item_links"] = await loop.run_in_executor(
            None,
            self._link_added_alexa_items,
            state["item_links"],
            state["ha_list"],
            added_alexa_items,
        )
        if added_alexa_items:
            if state["item_links"] != state["previous_item_links"]:
                await self._debug_log_entry(
                    logger,
                    "Item links after add-response linking: "
                    + json.dumps(
                        self._compact_links_for_log(
                            state["item_links"],
                            state["ha_list"],
                            state["alexa_snapshot"],
                            added_alexa_items,
                        )
                    ),
                )
            else:
                await self._debug_log_entry(
                    logger,
                    "Add-response linking left links unchanged for added Alexa items: "
                    + json.dumps(self._compact_alexa_snapshot_for_log(added_alexa_items)),
                )

        state["previous_item_links"] = dict(state["item_links"])
        return state

    async def _sync_phase_refresh_and_apply_ha(self, loop, state, logger=None):
        state["refreshed_snapshot"] = self._normalize_alexa_items(await self._get_list())
        state["refreshed_items"] = self._active_alexa_item_names(state["refreshed_snapshot"])
        await self._debug_log_entry(logger, "Refreshed Alexa list: " + json.dumps(state["refreshed_items"]))

        state["unlinked_refreshed_items"] = await loop.run_in_executor(
            None,
            self._filter_unlinked_alexa_active_names,
            state["refreshed_snapshot"],
            state["item_links"],
            state["ha_list"],
        )
        ignored_refreshed_removed_counts = Counter(
            item.get("name") if isinstance(item, dict) else item
            for item in state["to_complete"]
        )
        if await loop.run_in_executor(
            None,
            self._mark_items_completed_from_count_delta,
            state["unlinked_ha_list"],
            state["unlinked_alexa_list"],
            state["unlinked_refreshed_items"],
            ignored_refreshed_removed_counts,
        ):
            await self._debug_log_entry(logger, "Marked HA items as completed from refreshed Alexa delta")

        await self._debug_log_entry(logger, "Exporting new HA shopping list")
        await loop.run_in_executor(
            None,
            self._update_completed_ledger,
            lambda ledger: [
                self._mark_ledger_item_seen_on_alexa(ledger, item_name, state["refreshed_items"])
                for item_name in state["refreshed_items"]
            ],
        )
        state["completed_ledger"] = await loop.run_in_executor(None, self._get_completed_ledger)
        state["protected_refreshed_items"] = []
        state["desired_ha_list"] = await loop.run_in_executor(
            None,
            self._strip_names_from_ha_list,
            state["filtered_ha_list"],
            state["protected_refreshed_items"],
        )
        await self._debug_log_entry(
            logger, "Desired HA list before merge: " + json.dumps(state["desired_ha_list"])
        )

        linked_ha_items = [
            item for item in state["desired_ha_list"] if item.get("id") in state["item_links"]
        ]
        unlinked_desired_ha_list = await loop.run_in_executor(
            None,
            self._filter_unlinked_ha_items,
            state["desired_ha_list"],
            state["item_links"],
            state["refreshed_snapshot"],
        )
        visible_refreshed_items = await loop.run_in_executor(
            None,
            self._filter_alexa_items,
            state["unlinked_refreshed_items"],
            state["protected_refreshed_items"],
        )
        merged_unlinked_ha_list = await loop.run_in_executor(
            None,
            self._merge_ha_with_alexa,
            unlinked_desired_ha_list,
            visible_refreshed_items,
            state["local_complete_ha_counts"],
        )
        state["merged_ha_list"] = await loop.run_in_executor(
            None,
            self._dedupe_ha_items_by_id,
            linked_ha_items + merged_unlinked_ha_list,
        )
        await self._debug_log_entry(
            logger, "Merged HA list before apply: " + json.dumps(state["merged_ha_list"])
        )

        state["applied_ha_list"] = await self._apply_ha_shopping_list(state["merged_ha_list"])
        state["item_links"] = await loop.run_in_executor(
            None,
            self._remap_links_to_applied_ha_ids,
            state["item_links"],
            state["merged_ha_list"],
            state["applied_ha_list"],
        )
        state["item_links"] = await loop.run_in_executor(
            None,
            self._prune_item_links,
            state["item_links"],
            state["applied_ha_list"],
            state["refreshed_snapshot"],
        )
        if state["item_links"] != state["previous_item_links"]:
            await self._debug_log_entry(
                logger,
                "Item links after final prune: "
                + json.dumps(
                    self._compact_links_for_log(
                        state["item_links"], state["applied_ha_list"], state["refreshed_snapshot"]
                    )
                ),
            )

        state["previous_item_links"] = dict(state["item_links"])
        state["item_links"] = await loop.run_in_executor(
            None,
            self._bootstrap_item_links,
            state["item_links"],
            state["applied_ha_list"],
            state["refreshed_snapshot"],
        )
        if state["item_links"] != state["previous_item_links"]:
            await self._debug_log_entry(
                logger,
                "Item links after final bootstrap: "
                + json.dumps(
                    self._compact_links_for_log(
                        state["item_links"], state["applied_ha_list"], state["refreshed_snapshot"]
                    )
                ),
            )

        return state

    async def _sync_phase_finalize(self, loop, state, logger=None):
        await loop.run_in_executor(
            None,
            self._set_sync_snapshot,
            state["refreshed_snapshot"],
            state["applied_ha_list"],
        )
        await loop.run_in_executor(None, self._set_item_links, state["item_links"])
        if self._hasl_refresh is not None:
            await self._hasl_refresh()

        await self._debug_log_entry(logger, "Original list hash: " + state["original_ha_list_hash"])
        new_ha_list_hash = self._ha_items_hash(state["applied_ha_list"])
        await self._debug_log_entry(logger, "New list hash: " + new_ha_list_hash)
        if state["original_ha_list_hash"] != new_ha_list_hash:
            await self._debug_log_entry(logger, "List changed")
            return True

        await self._debug_log_entry(logger, "List did not change")
        return False


    async def _do_sync(self, loop, logger=None, force=False):
        state = await self._sync_phase_load_state(loop, logger, force)
        state = await self._sync_phase_prepare_links(loop, state, logger)
        state = await self._sync_phase_apply_remote_changes(loop, state, logger)
        state = await self._sync_phase_collect_unlinked_views(loop, state)
        state = await self._sync_phase_plan_alexa_changes(loop, state, logger)
        state = await self._sync_phase_apply_alexa_changes(loop, state, logger)
        state = await self._sync_phase_refresh_and_apply_ha(loop, state, logger)
        return await self._sync_phase_finalize(loop, state, logger)

    
    async def sync(self, logger=None, force=False):
        loop = asyncio.get_running_loop()

        if self._hass is None and os.path.exists(self._hasl_path) == False:
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
