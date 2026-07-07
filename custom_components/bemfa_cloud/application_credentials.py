"""Application credentials for Bemfa Cloud OAuth."""

from __future__ import annotations

from homeassistant.components.application_credentials import ClientCredential
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import (
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_TOKEN_URL,
)


async def async_get_default_credentials(hass: HomeAssistant) -> ClientCredential:
    """Return default credentials for Bemfa Cloud OAuth."""

    return ClientCredential(OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET)


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return the auth implementation."""

    return config_entry_oauth2_flow.LocalOAuth2Implementation(
        hass,
        auth_domain,
        credential.client_id,
        credential.client_secret,
        OAUTH_AUTHORIZE_URL,
        OAUTH_TOKEN_URL,
    )
