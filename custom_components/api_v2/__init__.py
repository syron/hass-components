"""Rest API for Home Assistant."""
import asyncio
from http import HTTPStatus
import logging

from aiohttp import web
from aiohttp.web_exceptions import HTTPBadRequest
import async_timeout
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.bootstrap import DATA_LOGGING
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    MATCH_ALL
)
import homeassistant.core as ha
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound, TemplateError, Unauthorized
from homeassistant.helpers import template
from homeassistant.helpers.json import json_dumps, json_loads
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

ATTR_BASE_URL = "base_url"
ATTR_EXTERNAL_URL = "external_url"
ATTR_INTERNAL_URL = "internal_url"
ATTR_LOCATION_NAME = "location_name"
ATTR_INSTALLATION_TYPE = "installation_type"
ATTR_REQUIRES_API_PASSWORD = "requires_api_password"
ATTR_UUID = "uuid"
ATTR_VERSION = "version"

DOMAIN = "customapi"

STREAM_PING_PAYLOAD = "ping"
STREAM_PING_INTERVAL = 50  # seconds


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the API with the HTTP interface."""
    hass.http.register_view(APIStatusView)
    hass.http.register_view(APIStatesView)

    if DATA_LOGGING in hass.data:
        hass.http.register_view(APIErrorLog)

    return True


class APIStatusView(HomeAssistantView):
    """View to handle Status requests."""

    url = "/api_v2/"
    name = "apiv2:status"

    @ha.callback
    def get(self, request):
        """Retrieve if API is running."""
        return self.json_message("API v2 running.")

class APIStatesView(HomeAssistantView):
    """View to handle States requests."""

    url = "/api_v2/states"
    name = "apiv2:states"

    @ha.callback
    def get(self, request):
        """Get current states."""
        user = request["hass_user"]
        entity_perm = user.permissions.check_entity
        states = [
            state
            for state in request.app["hass"].states.async_all()
            if entity_perm(state.entity_id, "read")
        ]
        return self.json(states)


class APIEntityStateView(HomeAssistantView):
    """View to handle EntityState requests."""

    url = "/api_v2/states/{entity_id}"
    name = "apiv2:entity-state"

    @ha.callback
    def get(self, request, entity_id):
        """Retrieve state of entity."""
        user = request["hass_user"]
        if not user.permissions.check_entity(entity_id, POLICY_READ):
            raise Unauthorized(entity_id=entity_id)

        if state := request.app["hass"].states.get(entity_id):
            return self.json(state)
        return self.json_message("Entity not found.", HTTPStatus.NOT_FOUND)

    async def post(self, request, entity_id):
        """Update state of entity."""
        if not request["hass_user"].is_admin:
            raise Unauthorized(entity_id=entity_id)
        hass = request.app["hass"]
        try:
            data = await request.json()
        except ValueError:
            return self.json_message("Invalid JSON specified.", HTTPStatus.BAD_REQUEST)

        if (new_state := data.get("state")) is None:
            return self.json_message("No state specified.", HTTPStatus.BAD_REQUEST)

        attributes = data.get("attributes")
        force_update = data.get("force_update", False)

        is_new_state = hass.states.get(entity_id) is None

        # Write state
        hass.states.async_set(
            entity_id, new_state, attributes, force_update, self.context(request)
        )

        # Read the state back for our response
        status_code = HTTPStatus.CREATED if is_new_state else HTTPStatus.OK
        resp = self.json(hass.states.get(entity_id), status_code)

        resp.headers.add("Location", f"/api/states/{entity_id}")

        return resp

    @ha.callback
    def delete(self, request, entity_id):
        """Remove entity."""
        if not request["hass_user"].is_admin:
            raise Unauthorized(entity_id=entity_id)
        if request.app["hass"].states.async_remove(entity_id):
            return self.json_message("Entity removed.")
        return self.json_message("Entity not found.", HTTPStatus.NOT_FOUND)

class APIErrorLog(HomeAssistantView):
    """View to fetch the API error log."""

    url = "/api_v2/errors"
    name = "api:error_log"

    async def get(self, request):
        """Retrieve API error log."""
        if not request["hass_user"].is_admin:
            raise Unauthorized()
        return web.FileResponse(request.app["hass"].data[DATA_LOGGING])


async def async_services_json(hass):
    """Generate services data to JSONify."""
    descriptions = await async_get_all_descriptions(hass)
    return [{"domain": key, "services": value} for key, value in descriptions.items()]


@ha.callback
def async_events_json(hass):
    """Generate event data to JSONify."""
    return [
        {"event": key, "listener_count": value}
        for key, value in hass.bus.async_listeners().items()
    ]
