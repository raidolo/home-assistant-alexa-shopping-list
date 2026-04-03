#!/usr/bin/env python3

import asyncio
import websockets
import json
import signal
import os
import logging
import time
import datetime

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

from alexa import AlexaShoppingList, NotAuthenticatedError

logger = logging.getLogger(__name__)

clients = set()

alexa = None
config = {}
server = None

# ============================================================
# Helpers


def _time_now():
    return int(time.time())

# ============================================================
# Config


def _config_path():
    return os.environ.get(
        "ASL_CONFIG_PATH", 
        os.path.dirname(os.path.realpath(__file__))
    )


def _load_config():
    global config
    if os.path.exists(os.path.join(_config_path(), 'config.json')):
        with open(os.path.join(_config_path(), 'config.json'), 'r') as file:
            config = json.load(file)
            return
    config = {}


def _save_config():
    with open(os.path.join(_config_path(), 'config.json'), 'w') as file:
        json.dump(config, file)


def _get_config_value(key, default=None):
    if key in config:
        return config[key]
    return default


def _set_config_value(key, new_value=None):
    logger.info(f"Set config value `{key}` = {new_value}")
    global config
    if new_value is not None:
        config[key] = new_value
    elif key in config:
        del config[key]
    _save_config()


async def _cmd_config_valid():
    return os.path.exists(
        os.path.join(_config_path(), 'config.json')
    ), None


async def _cmd_config_set(args):
    _set_config_value(args['key'], args['value'])
    if args['key'] == "amazon_url":
        global alexa
        alexa = None
    return True, None


async def _cmd_config_get(args):
    return _get_config_value(args['key']), None

# ============================================================
# Alexa


def _get_alexa():
    global alexa
    if alexa is None:
        alexa = AlexaShoppingList(
            _get_config_value("amazon_url", "amazon.co.uk"),
            _config_path()
        )
    return alexa

# ============================================================
# API


async def _cmd_reset():
    purge_files = ['config.json', 'cookies.json']
    for filename in purge_files:
        file_path = os.path.join(_config_path(), filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    
    _load_config()
    global alexa
    alexa = None
    return True, None


async def _cmd_is_authenticated(arguments=None):
    arguments = arguments or {}
    force = arguments.get('force', False)
    
    if not force:
        recent = _get_config_value('auth_checked_time', 0)
        time_diff = _time_now() - recent

        if time_diff < 86400:
            return True, None

    instance = _get_alexa()

    requires_login = await asyncio.to_thread(instance.requires_login)

    if requires_login:
        logger.info("Authenticated: No")
        _set_config_value("auth_checked_time", 0)
        result = False, None
    else:
        logger.info("Authenticated: Yes")
        _set_config_value("auth_checked_time", _time_now())
        result = True, None
    return result


async def _cmd_login(args):
    logger.info("Attempting login...")

    with open(os.path.join(_config_path(), 'cookies.json'), 'w') as file:
        json.dump(args['session'], file)

    global alexa
    alexa = None
    return await _cmd_is_authenticated()


async def _cmd_get_shopping_list():
    try:
        instance = _get_alexa()
        requires_login = await asyncio.to_thread(instance.requires_login)
        if requires_login:
            result = None, "Not authenticated"
        else:
            result = await asyncio.to_thread(instance.get_alexa_list), None
    except NotAuthenticatedError as e:
        logger.warning(f"Session expired during get_list: {e}")
        _set_config_value("auth_checked_time", 0)
        result = None, "Not authenticated"
    except Exception as e:
        logger.error(f"Error getting shopping list: {e}", exc_info=True)
        result = None, f"Server error: {e}"
    return result


async def _cmd_get_add_shopping_list_item(args):
    try:
        instance = _get_alexa()
        requires_login = await asyncio.to_thread(instance.requires_login)
        if requires_login:
            result = None, "Not authenticated"
        else:
            result = await asyncio.to_thread(
                instance.add_alexa_list_item,
                args['item'],
                args.get('include_details', False)
            ), None
    except NotAuthenticatedError as e:
        logger.warning(f"Session expired during add_item: {e}")
        _set_config_value("auth_checked_time", 0)
        result = None, "Not authenticated"
    except Exception as e:
        logger.error(f"Error adding item: {e}", exc_info=True)
        result = None, f"Server error: {e}"
    return result


async def _cmd_get_update_shopping_list_item(args):
    try:
        instance = _get_alexa()
        requires_login = await asyncio.to_thread(instance.requires_login)
        if requires_login:
            result = None, "Not authenticated"
        else:
            result = await asyncio.to_thread(
                instance.update_alexa_list_item,
                args['old'],
                args['new'],
                args.get('alexa_id')
            ), None
    except NotAuthenticatedError as e:
        logger.warning(f"Session expired during update_item: {e}")
        _set_config_value("auth_checked_time", 0)
        result = None, "Not authenticated"
    except Exception as e:
        logger.error(f"Error updating item: {e}", exc_info=True)
        result = None, f"Server error: {e}"
    return result


async def _cmd_get_remove_shopping_list_item(args):
    try:
        instance = _get_alexa()
        requires_login = await asyncio.to_thread(instance.requires_login)
        if requires_login:
            result = None, "Not authenticated"
        else:
            result = await asyncio.to_thread(instance.remove_alexa_list_item, args['item']), None
    except NotAuthenticatedError as e:
        logger.warning(f"Session expired during remove_item: {e}")
        _set_config_value("auth_checked_time", 0)
        result = None, "Not authenticated"
    except Exception as e:
        logger.error(f"Error removing item: {e}", exc_info=True)
        result = None, f"Server error: {e}"
    return result


async def _cmd_get_complete_shopping_list_item(args):
    try:
        instance = _get_alexa()
        requires_login = await asyncio.to_thread(instance.requires_login)
        if requires_login:
            result = None, "Not authenticated"
        else:
            result = await asyncio.to_thread(
                instance.complete_alexa_list_item,
                args['item'],
                args.get('alexa_id')
            ), None
    except NotAuthenticatedError as e:
        logger.warning(f"Session expired during complete_item: {e}")
        _set_config_value("auth_checked_time", 0)
        result = None, "Not authenticated"
    except Exception as e:
        logger.error(f"Error completing item: {e}", exc_info=True)
        result = None, f"Server error: {e}"
    return result


async def _cmd_bulk_apply_shopping_list_changes(args):
    try:
        instance = _get_alexa()
        requires_login = await asyncio.to_thread(instance.requires_login)
        if requires_login:
            result = None, "Not authenticated"
        else:
            result = await asyncio.to_thread(
                instance.bulk_apply_alexa_list_changes,
                add_items=args.get('add_items', []),
                remove_items=args.get('remove_items', []),
                update_items=args.get('update_items', []),
                complete_items=args.get('complete_items', []),
                include_details=args.get('include_details', False)
            ), None
    except NotAuthenticatedError as e:
        logger.warning(f"Session expired during bulk_apply_changes: {e}")
        _set_config_value("auth_checked_time", 0)
        result = None, "Not authenticated"
    except Exception as e:
        logger.error(f"Error applying bulk list changes: {e}", exc_info=True)
        result = None, f"Server error: {e}"
    return result

# ============================================================
# Main handler


async def _route_command(command, arguments=None):
    arguments = arguments or {}

    # Config
    if command == "config_valid":
        return await _cmd_config_valid()
    if command == "config_set":
        return await _cmd_config_set(arguments)
    if command == "config_get":
        return await _cmd_config_get(arguments)
    if command == "reset":
        return await _cmd_reset()
    
    # Authentication
    if command == "authenticated":
        return await _cmd_is_authenticated(arguments)
    if command == "login":
        return await _cmd_login(arguments)
    
    # Shopping list
    if command == "get_list":
        return await _cmd_get_shopping_list()
    if command == "add_item":
        return await _cmd_get_add_shopping_list_item(arguments)
    if command == "update_item":
        return await _cmd_get_update_shopping_list_item(arguments)
    if command == "remove_item":
        return await _cmd_get_remove_shopping_list_item(arguments)
    if command == "complete_item":
        return await _cmd_get_complete_shopping_list_item(arguments)
    if command == "bulk_apply_changes":
        return await _cmd_bulk_apply_shopping_list_changes(arguments)
    
    # Misc
    if command == "ping":
        return "pong", None
    if command == "shutdown":
        await _shutdown_server()
        return True, None
    
    return None, f"Unknown command: {command}"


async def _process_command(websocket, path):
    clients.add(websocket)
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                command = data.get('command')
                arguments = data.get('args')

                response = {"result": None, "error": None}
                results = await _route_command(command, arguments)

                if results is not None and len(results) == 2:
                    response = {
                        "result": results[0],
                        "error": results[1]
                    }
                else:
                    response['error'] = 'Unknown command'

            except json.JSONDecodeError:
                response = {'result': None, 'error': 'Invalid JSON'}
            except Exception as e:
                logger.error(f"Error processing command: {e}", exc_info=True)
                response = {'result': None, 'error': f'Server error: {e}'}

            await websocket.send(json.dumps(response))
    finally:
        clients.discard(websocket)

# ============================================================
# Start/Stop


async def _shutdown_server():
    for ws in clients:
        await ws.close()
    server.close()
    await server.wait_closed()


def _signal_handler(sig, frame):
    logger.info("Shutting down server...")
    asyncio.run(_shutdown_server())


async def main():
    _load_config()

    global server
    global alexa
    listen_addr = None
    listen_port = int(_get_config_value('listen_port', 4000))
    alexa = AlexaShoppingList(
        _get_config_value("amazon_url", "amazon.co.uk"),
        _config_path()
    )
    server = await websockets.serve(_process_command, listen_addr, listen_port)

    logger.info("======================================================================")
    logger.info(f"Alexa Shopping List server started on port {listen_port}")

    signal.signal(signal.SIGINT, _signal_handler)
    await server.wait_closed()

# ============================================================


if __name__ == "__main__":
    asyncio.run(main())
