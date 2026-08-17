"""Home Assistant backend over the WebSocket API.

One connection carries everything: the initial ``get_states`` dump, the
``state_changed`` subscription, and outgoing ``call_service`` commands.  It
runs on a private asyncio loop in a daemon thread; the UI thread only ever
touches the snapshot store inherited from :class:`Backend`.

Auth uses a long-lived access token (Home Assistant profile page -> Security
-> Long-lived access tokens).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .base import Backend, Entity, Link
from .registry import RegistryPlan

RECONNECT_MIN = 2.0
RECONNECT_MAX = 30.0
COMMAND_TIMEOUT = 10.0


def websocket_url(base_url: str) -> str:
    """``http://ha.local:8123`` -> ``ws://ha.local:8123/api/websocket``."""
    parts = urlsplit(base_url.rstrip("/"))
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(parts.scheme)
    if scheme is None:
        raise ValueError(f"unsupported Home Assistant URL scheme: {base_url!r}")
    path = parts.path or ""
    if not path.endswith("/api/websocket"):
        path = path.rstrip("/") + "/api/websocket"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def _entity_from_state(payload: dict[str, Any]) -> Entity:
    return Entity(
        entity_id=payload["entity_id"],
        state=str(payload.get("state", "")),
        attributes=dict(payload.get("attributes") or {}),
    )


class HomeAssistantBackend(Backend):
    def __init__(
        self,
        url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        entity_filter: list[str] | None = None,
        registry: RegistryPlan | None = None,
    ) -> None:
        super().__init__()
        if not token:
            raise ValueError("Home Assistant backend requires a long-lived access token")
        self.url = url
        self.ws_url = websocket_url(url)
        self._token = token
        self._verify_ssl = verify_ssl
        #: optional allow-list of entity_id prefixes, keeps big installs light
        self._filter = tuple(entity_filter or ())
        #: desired floor/area/label layout, pushed once per connection
        self._registry = registry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id = 0
        self._id_lock = threading.Lock()
        #: in-flight request/response commands, keyed by message id
        self._pending: dict[int, asyncio.Future] = {}

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stopping.clear()
        self._set_link(Link.CONNECTING)
        self._thread = threading.Thread(target=self._run, name="ha-backend", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
        self._thread = None
        self._set_link(Link.OFFLINE)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._supervise())
        except RuntimeError:
            pass  # loop.stop() during shutdown
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._loop = None

    async def _supervise(self) -> None:
        """Connect, and keep reconnecting with exponential backoff."""
        delay = RECONNECT_MIN
        connector_ssl = None if self._verify_ssl else False
        while not self._stopping.is_set():
            try:
                self._set_link(Link.CONNECTING)
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(self._ws_url_for_request(), ssl=connector_ssl) as ws:
                        self._ws = ws
                        await self._session(ws)
                delay = RECONNECT_MIN
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to the operator
                self._set_link(Link.OFFLINE, f"{type(exc).__name__}: {exc}")
                self.raise_alert("ha.link", "HOME ASSISTANT LINK LOST", "warning")
            finally:
                self._ws = None
            if self._stopping.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(RECONNECT_MAX, delay * 1.8)

    def _ws_url_for_request(self) -> str:
        return self.ws_url

    def _next_id(self) -> int:
        with self._id_lock:
            self._msg_id += 1
            return self._msg_id

    async def _session(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        hello = await ws.receive_json()
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting: {hello.get('type')}")
        await ws.send_json({"type": "auth", "access_token": self._token})
        auth = await ws.receive_json()
        if auth.get("type") != "auth_ok":
            raise RuntimeError(auth.get("message", "authentication rejected"))

        self._set_link(Link.ONLINE)
        self.clear_alert("ha.link")

        states_id = self._next_id()
        await ws.send_json({"id": states_id, "type": "get_states"})
        subscribe_id = self._next_id()
        await ws.send_json(
            {"id": subscribe_id, "type": "subscribe_events", "event_type": "state_changed"}
        )

        # Reconciling the registry needs replies, so it runs alongside the
        # message pump rather than before it - awaiting it here would deadlock
        # on results this loop has not started reading yet.
        syncer = asyncio.ensure_future(self._sync_registry(ws))

        try:
            await self._pump(ws, states_id)
        finally:
            syncer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await syncer
            self._fail_pending(ConnectionError("websocket closed"))

    async def _pump(self, ws: aiohttp.ClientWebSocketResponse, states_id: int) -> None:
        async for message in ws:
            if message.type is aiohttp.WSMsgType.TEXT:
                self._handle(message.json(), states_id)
            elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        raise ConnectionError("websocket closed")

    def _fail_pending(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _request(
        self, ws: aiohttp.ClientWebSocketResponse, type_: str, **payload: Any
    ) -> Any:
        """Send a command and wait for its ``result``.

        Only for the registry work: state traffic stays fire-and-forget so a
        slow reply can never stall the UI.
        """
        loop = asyncio.get_running_loop()
        msg_id = self._next_id()
        future: asyncio.Future = loop.create_future()
        self._pending[msg_id] = future
        try:
            await ws.send_json({"id": msg_id, "type": type_, **payload})
            return await asyncio.wait_for(future, COMMAND_TIMEOUT)
        finally:
            self._pending.pop(msg_id, None)

    def _handle(self, payload: dict[str, Any], states_id: int) -> None:
        kind = payload.get("type")
        if kind == "result":
            future = self._pending.get(payload.get("id"))
            if future is not None:
                if future.done():
                    return
                if payload.get("success"):
                    future.set_result(payload.get("result"))
                else:
                    message = (payload.get("error") or {}).get("message", "command rejected")
                    future.set_exception(RuntimeError(message))
                return
            if payload.get("id") == states_id and payload.get("success"):
                self._publish_many(
                    [_entity_from_state(s) for s in payload.get("result") or [] if self._wanted(s["entity_id"])]
                )
            elif not payload.get("success", True):
                error = (payload.get("error") or {}).get("message", "command rejected")
                self.raise_alert("ha.command", f"HA: {error}", "caution")
        elif kind == "event":
            data = (payload.get("event") or {}).get("data") or {}
            entity_id = data.get("entity_id")
            if not entity_id or not self._wanted(entity_id):
                return
            new_state = data.get("new_state")
            if new_state is None:
                self._drop(entity_id)
            else:
                self._publish(_entity_from_state(new_state))

    def _wanted(self, entity_id: str) -> bool:
        return not self._filter or entity_id.startswith(self._filter)

    # -- registry reconciliation -----------------------------------------
    async def _sync_registry(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Make the HA registry match the plan: floors, areas, labels, entities.

        Additive by design.  It creates what is missing and re-files entities
        the plan owns; it never deletes a floor, area or label, and it never
        touches an entity the plan does not mention.  Labels a person added by
        hand survive - only labels this plan manages are swapped out.

        A failure here is reported and dropped: the house must stay operable
        when the registry cannot be written (a read-only token, say).
        """
        plan = self._registry
        if plan is None or plan.is_empty:
            return
        try:
            floor_ids = await self._ensure_floors(ws, plan)
            area_ids = await self._ensure_areas(ws, plan, floor_ids)
            label_ids = await self._ensure_labels(ws, plan)
            changed = await self._assign_entities(ws, plan, area_ids, label_ids)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            self.raise_alert("ha.registry", f"HA REGISTRY SYNC FAILED: {exc}", "caution")
            return
        self.clear_alert("ha.registry")
        if changed:
            self.raise_alert("ha.registry", f"HA REGISTRY UPDATED: {changed} ENTITIES", "info")

    @staticmethod
    def _by_name(rows: Any, id_key: str) -> dict[str, str]:
        """``{lowercased name: id}`` - HA names are free text, humans are not."""
        return {str(r.get("name", "")).strip().lower(): r[id_key] for r in rows or [] if r.get(id_key)}

    async def _ensure_floors(self, ws: aiohttp.ClientWebSocketResponse, plan: RegistryPlan) -> dict[str, str]:
        existing = self._by_name(await self._request(ws, "config/floor_registry/list"), "floor_id")
        out: dict[str, str] = {}
        for name in plan.floors:
            key = name.strip().lower()
            if key not in existing:
                created = await self._request(ws, "config/floor_registry/create", name=name)
                existing[key] = created["floor_id"]
            out[name] = existing[key]
        return out

    async def _ensure_areas(
        self, ws: aiohttp.ClientWebSocketResponse, plan: RegistryPlan, floor_ids: dict[str, str]
    ) -> dict[str, str]:
        rows = await self._request(ws, "config/area_registry/list") or []
        existing = self._by_name(rows, "area_id")
        current_floor = {r["area_id"]: r.get("floor_id") for r in rows if r.get("area_id")}
        out: dict[str, str] = {}
        for spec in plan.areas:
            key = spec.name.strip().lower()
            floor_id = floor_ids.get(spec.floor)
            if key not in existing:
                created = await self._request(
                    ws, "config/area_registry/create", name=spec.name, floor_id=floor_id
                )
                existing[key] = created["area_id"]
            elif floor_id and current_floor.get(existing[key]) != floor_id:
                # the plan file is the source of truth for which storey an area is on
                await self._request(
                    ws, "config/area_registry/update", area_id=existing[key], floor_id=floor_id
                )
            out[spec.name] = existing[key]
        return out

    async def _ensure_labels(self, ws: aiohttp.ClientWebSocketResponse, plan: RegistryPlan) -> dict[str, str]:
        existing = self._by_name(await self._request(ws, "config/label_registry/list"), "label_id")
        out: dict[str, str] = {}
        for name in plan.labels:
            key = name.strip().lower()
            if key not in existing:
                created = await self._request(ws, "config/label_registry/create", name=name)
                existing[key] = created["label_id"]
            out[name] = existing[key]
        return out

    async def _assign_entities(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        plan: RegistryPlan,
        area_ids: dict[str, str],
        label_ids: dict[str, str],
    ) -> int:
        """File each planned entity into its area and room label.

        The whole registry is listed once rather than fetched per entity: a
        plan with thirty devices should not cost thirty round-trips.
        """
        rows = await self._request(ws, "config/entity_registry/list") or []
        entries = {r["entity_id"]: r for r in rows if r.get("entity_id")}
        owned = {label_ids[name] for name in plan.labels if name in label_ids}

        changed = 0
        missing: list[str] = []
        for spec in plan.entities:
            entry = entries.get(spec.entity_id)
            if entry is None:
                # not in the registry at all: a YAML entity without a unique_id,
                # or an entity_id in the plan that does not exist yet
                missing.append(spec.entity_id)
                continue
            area_id = area_ids.get(spec.area)
            wanted_labels = {label_ids[name] for name in spec.labels if name in label_ids}
            current = set(entry.get("labels") or [])
            # keep hand-made labels, replace only the room labels we manage
            merged = (current - owned) | wanted_labels

            update: dict[str, Any] = {}
            if area_id and entry.get("area_id") != area_id:
                update["area_id"] = area_id
            if merged != current:
                update["labels"] = sorted(merged)
            if not update:
                continue
            await self._request(
                ws, "config/entity_registry/update", entity_id=spec.entity_id, **update
            )
            changed += 1

        if missing:
            self.raise_alert(
                "ha.registry.missing",
                f"{len(missing)} PLANNED ENTITIES NOT IN HA REGISTRY",
                "info",
            )
        else:
            self.clear_alert("ha.registry.missing")
        return changed

    # -- commands (called from the UI thread) ----------------------------
    def call(
        self, domain: str, service: str, entity_id: str | list[str] | None = None, **data: Any
    ) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            self.raise_alert("ha.command", "HA: NOT CONNECTED", "caution")
            return
        message: dict[str, Any] = {
            "id": self._next_id(),
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": data,
        }
        if entity_id:
            message["target"] = {"entity_id": entity_id}
        asyncio.run_coroutine_threadsafe(self._send(message), loop)

    async def _send(self, message: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            self.raise_alert("ha.command", "HA: NOT CONNECTED", "caution")
            return
        try:
            await asyncio.wait_for(ws.send_json(message), COMMAND_TIMEOUT)
            self.clear_alert("ha.command")
        except Exception as exc:  # noqa: BLE001
            self.raise_alert("ha.command", f"HA SEND FAILED: {exc}", "caution")
