"""HTTP views for the Zenbi integration."""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZenbiFileDownloadView(HomeAssistantView):
    """View to proxy and stream files from Zenbi with inline content disposition."""

    url = "/api/zenbi/file/{entry_id}/{file_id}"
    name = "api:zenbi:file"
    requires_auth = True

    async def get(
        self, request: web.Request, entry_id: str, file_id: str
    ) -> web.StreamResponse:
        """Handle download request and stream content with Content-Disposition: inline."""
        hass: HomeAssistant = request.app["hass"]
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if not coordinator:
            return web.Response(status=404, text="Zenbi integration entry not found")

        try:
            download_url = (
                await coordinator.client.get_weekly_schedule_file_download_url(file_id)
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to retrieve file download URL for file %s (entry %s): %s",
                file_id,
                entry_id,
                err,
            )
            return web.Response(status=502, text="Failed to retrieve file from Zenbi")

        try:
            session = coordinator.client.session
            async with session.get(download_url) as upstream_resp:
                if upstream_resp.status != 200:
                    _LOGGER.warning(
                        "Upstream storage returned status %s for file %s",
                        upstream_resp.status,
                        file_id,
                    )
                    return web.Response(
                        status=upstream_resp.status,
                        text="Failed to fetch file from storage",
                    )

                content_type = upstream_resp.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                headers = {
                    "Content-Type": content_type,
                    "Content-Disposition": "inline",
                    "Cache-Control": "public, max-age=3600",
                }
                body = await upstream_resp.read()
                return web.Response(
                    status=200,
                    body=body,
                    headers=headers,
                )
        except Exception as err:
            _LOGGER.error(
                "Failed to stream file %s (entry %s): %s",
                file_id,
                entry_id,
                err,
            )
            return web.Response(status=502, text="Failed to stream file from storage")

