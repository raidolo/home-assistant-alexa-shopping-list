#!/usr/bin/env python3

import asyncio
import datetime
import json
import logging
import os
import signal
import time

import websockets

from alexa import AlexaShoppingList, NotAuthenticatedError


class _CustomFormatter(logging.Formatter):
    def format(self, record):
        dt = datetime.datetime.fromtimestamp(record.created)
        time_str = dt.strftime("%Y/%m/%d %H:%M:%S.%f")
        level_str = record.levelname.lower()
        return f"{time_str} [{level_str}] {record.getMessage()}"


_handler = logging.StreamHandler()
_handler.setFormatter(_CustomFormatter())
logging.root.setLevel(logging.INFO)
for _h in logging.root.handlers[:]:
    logging.root.removeHandler(_h)
logging.root.addHandler(_handler)

logger = logging.getLogger(__name__)


class ServerApp:
    def __init__(self):
        self.clients = set()
        self.config = {}
        self.server = None
        self.alexa = None
        self.loop = None
        self._shutdown_requested = False

        self._command_handlers = {
            "config_valid": self._cmd_config_valid,
            "config_set": self._cmd_config_set,
            "config_get": self._cmd_config_get,
            "reset": self._cmd_reset,
            "authenticated": self._cmd_is_authenticated,
            "login": self._cmd_login,
            "get_list": self._cmd_get_shopping_list,
            "add_item": self._cmd_add_shopping_list_item,
            "update_item": self._cmd_update_shopping_list_item,
            "remove_item": self._cmd_remove_shopping_list_item,
            "complete_item": self._cmd_complete_shopping_list_item,
            "bulk_apply_changes": self._cmd_bulk_apply_shopping_list_changes,
            "ping": self._cmd_ping,
            "shutdown": self._cmd_shutdown,
        }

    # ============================================================
    # Helpers

    def _time_now(self):
        return int(time.time())

    def _config_path(self):
        return os.environ.get(
            "ASL_CONFIG_PATH",
            os.path.dirname(os.path.realpath(__file__)),
        )

    def _config_file_path(self):
        return os.path.join(self._config_path(), "config.json")

    def _cookies_file_path(self):
        return os.path.join(self._config_path(), "cookies.json")

    # ============================================================
    # Config

    def _load_config(self):
        if os.path.exists(self._config_file_path()):
            with open(self._config_file_path(), "r", encoding="utf-8") as file:
                self.config = json.load(file)
                return
        self.config = {}

    def _save_config(self):
        with open(self._config_file_path(), "w", encoding="utf-8") as file:
            json.dump(self.config, file)

    def _get_config_value(self, key, default=None):
        return self.config.get(key, default)

    def _set_config_value(self, key, new_value=None):
        logger.info(f"Set config value `{key}` = {new_value}")
        if new_value is not None:
            self.config[key] = new_value
        else:
            self.config.pop(key, None)
        self._save_config()

        if key == "amazon_url":
            self.alexa = None

    # ============================================================
    # Alexa

    def _get_alexa(self):
        if self.alexa is None:
            self.alexa = AlexaShoppingList(
                self._get_config_value("amazon_url", "amazon.co.uk"),
                self._config_path(),
            )
        return self.alexa

    # ============================================================
    # API

    async def _cmd_config_valid(self, arguments=None):
        del arguments
        return os.path.exists(self._config_file_path()), None

    async def _cmd_config_set(self, args):
        self._set_config_value(args["key"], args["value"])
        return True, None

    async def _cmd_config_get(self, args):
        return self._get_config_value(args["key"]), None

    async def _cmd_reset(self, arguments=None):
        del arguments
        purge_files = ["config.json", "cookies.json"]
        for filename in purge_files:
            file_path = os.path.join(self._config_path(), filename)
            if os.path.exists(file_path):
                os.remove(file_path)

        self._load_config()
        self.alexa = None
        return True, None

    async def _cmd_is_authenticated(self, arguments=None):
        arguments = arguments or {}
        force = arguments.get("force", False)

        if not force:
            recent = self._get_config_value("auth_checked_time", 0)
            time_diff = self._time_now() - recent

            if time_diff < 86400:
                return True, None

        instance = self._get_alexa()
        requires_login = await asyncio.to_thread(instance.requires_login)

        if requires_login:
            logger.info("Authenticated: No")
            self._set_config_value("auth_checked_time", 0)
            return False, None

        logger.info("Authenticated: Yes")
        self._set_config_value("auth_checked_time", self._time_now())
        return True, None

    async def _cmd_login(self, args):
        logger.info("Attempting login...")

        with open(self._cookies_file_path(), "w", encoding="utf-8") as file:
            json.dump(args["session"], file)

        self.alexa = None
        return await self._cmd_is_authenticated()

    async def _run_with_authenticated_alexa(self, action_name, callback):
        try:
            instance = self._get_alexa()
            requires_login = await asyncio.to_thread(instance.requires_login)
            if requires_login:
                return None, "Not authenticated"
            result = await callback(instance)
            return result, None
        except NotAuthenticatedError as error:
            logger.warning(f"Session expired during {action_name}: {error}")
            self._set_config_value("auth_checked_time", 0)
            return None, "Not authenticated"
        except Exception as error:
            logger.error(f"Error during {action_name}: {error}", exc_info=True)
            return None, f"Server error: {error}"

    async def _cmd_get_shopping_list(self, arguments=None):
        del arguments
        return await self._run_with_authenticated_alexa(
            "get_list",
            lambda instance: asyncio.to_thread(instance.get_alexa_list),
        )

    async def _cmd_add_shopping_list_item(self, args):
        return await self._run_with_authenticated_alexa(
            "add_item",
            lambda instance: asyncio.to_thread(
                instance.add_alexa_list_item,
                args["item"],
                args.get("include_details", False),
            ),
        )

    async def _cmd_update_shopping_list_item(self, args):
        return await self._run_with_authenticated_alexa(
            "update_item",
            lambda instance: asyncio.to_thread(
                instance.update_alexa_list_item,
                args["old"],
                args["new"],
                args.get("alexa_id"),
            ),
        )

    async def _cmd_remove_shopping_list_item(self, args):
        return await self._run_with_authenticated_alexa(
            "remove_item",
            lambda instance: asyncio.to_thread(
                instance.remove_alexa_list_item,
                args["item"],
            ),
        )

    async def _cmd_complete_shopping_list_item(self, args):
        return await self._run_with_authenticated_alexa(
            "complete_item",
            lambda instance: asyncio.to_thread(
                instance.complete_alexa_list_item,
                args["item"],
                args.get("alexa_id"),
            ),
        )

    async def _cmd_bulk_apply_shopping_list_changes(self, args):
        return await self._run_with_authenticated_alexa(
            "bulk_apply_changes",
            lambda instance: asyncio.to_thread(
                instance.bulk_apply_alexa_list_changes,
                add_items=args.get("add_items", []),
                remove_items=args.get("remove_items", []),
                update_items=args.get("update_items", []),
                complete_items=args.get("complete_items", []),
                include_details=args.get("include_details", False),
            ),
        )

    async def _cmd_ping(self, arguments=None):
        del arguments
        return "pong", None

    async def _cmd_shutdown(self, arguments=None):
        del arguments
        await self._shutdown_server()
        return True, None

    # ============================================================
    # Main handler

    async def _route_command(self, command, arguments=None):
        arguments = arguments or {}
        handler = self._command_handlers.get(command)
        if handler is None:
            return None, f"Unknown command: {command}"
        return await handler(arguments)

    async def _process_command(self, websocket, path=None):
        del path
        self.clients.add(websocket)
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    command = data.get("command")
                    arguments = data.get("args")

                    result, error = await self._route_command(command, arguments)
                    response = {
                        "result": result,
                        "error": error,
                    }
                except json.JSONDecodeError:
                    response = {"result": None, "error": "Invalid JSON"}
                except Exception as error:
                    logger.error(f"Error processing command: {error}", exc_info=True)
                    response = {"result": None, "error": f"Server error: {error}"}

                await websocket.send(json.dumps(response))
        finally:
            self.clients.discard(websocket)

    # ============================================================
    # Start/Stop

    async def _shutdown_server(self):
        if self._shutdown_requested:
            return

        self._shutdown_requested = True

        for ws in list(self.clients):
            await ws.close()

        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    def _request_shutdown(self):
        logger.info("Shutting down server...")
        if self.loop is not None:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._shutdown_server())
            )

    async def main(self):
        self._load_config()
        self.loop = asyncio.get_running_loop()

        listen_addr = None
        listen_port = int(self._get_config_value("listen_port", 4000))
        self.alexa = AlexaShoppingList(
            self._get_config_value("amazon_url", "amazon.co.uk"),
            self._config_path(),
        )
        self.server = await websockets.serve(self._process_command, listen_addr, listen_port)

        logger.info("======================================================================")
        logger.info(f"Alexa Shopping List server started on port {listen_port}")

        signal.signal(signal.SIGINT, lambda sig, frame: self._request_shutdown())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda sig, frame: self._request_shutdown())

        await self.server.wait_closed()


if __name__ == "__main__":
    asyncio.run(ServerApp().main())
