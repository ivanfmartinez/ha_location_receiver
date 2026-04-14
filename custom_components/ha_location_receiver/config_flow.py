"""Config flow for Location Receiver integration."""
from __future__ import annotations

import re
import secrets

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_BOUND_DEVICE_ID,
    CONF_CUSTOM_WEBHOOK_ID,
    CONF_DEVICE_TYPE,
    CONF_GLOBAL_WEBHOOK_ID,
    CONF_GLOBAL_WEBHOOK_MANUAL,
    CONF_OSMAND_MODE,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_MANUAL,
    DEVICE_TYPES,
    DEVICE_TYPE_OSMAND,
    DOMAIN,
    OSMAND_MODE_GLOBAL,
    OSMAND_MODE_INDIVIDUAL,
    OSMAND_MODES,
    WEBHOOK_ID_MIN_LENGTH,
)

_WEBHOOK_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_webhook_id(
    custom_id: str,
    current_entries,
    current_entry_id: str | None = None,
) -> str | None:
    """Validate a custom webhook ID. Returns error key or None."""
    if len(custom_id) < WEBHOOK_ID_MIN_LENGTH:
        return "webhook_too_short"
    if not _WEBHOOK_ID_RE.match(custom_id):
        return "webhook_invalid_chars"
    for entry in current_entries:
        if entry.entry_id == current_entry_id:
            continue
        if entry.data.get(CONF_WEBHOOK_ID) == custom_id:
            return "webhook_already_used"
        if entry.data.get(CONF_GLOBAL_WEBHOOK_ID) == custom_id:
            return "webhook_already_used"
    return None


def _build_webhook_url(hass, webhook_id: str) -> str:
    """Build the full webhook URL."""
    try:
        base = (hass.config.internal_url or hass.config.external_url or "").rstrip("/")
    except Exception:
        base = ""
    return f"{base}/api/webhook/{webhook_id}" if base else f"/api/webhook/{webhook_id}"


# ──────────────────────────────────────────────────────────────────────
# Config Flow (initial setup)
# ──────────────────────────────────────────────────────────────────────

class GpsTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Location Receiver."""

    VERSION = 1

    def __init__(self) -> None:
        self._device_name: str = ""
        self._device_type: str = DEVICE_TYPE_OSMAND
        self._osmand_mode: str = OSMAND_MODE_INDIVIDUAL
        self._bound_device_id: str = ""

    # ------------------------------------------------------------------
    # Step 1 – Device name and type
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            for entry in self._async_current_entries():
                if entry.title.lower() == name.lower():
                    errors[CONF_NAME] = "name_exists"
                    break

            if not errors:
                self._device_name = name
                self._device_type = user_input[CONF_DEVICE_TYPE]
                if self._device_type == DEVICE_TYPE_OSMAND:
                    return await self.async_step_osmand_mode()
                return await self.async_step_webhook()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=self._device_name): str,
                vol.Required(CONF_DEVICE_TYPE, default=self._device_type): vol.In(DEVICE_TYPES),
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2a – OsmAnd mode
    # ------------------------------------------------------------------

    async def async_step_osmand_mode(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._osmand_mode = user_input[CONF_OSMAND_MODE]
            self._bound_device_id = (user_input.get(CONF_BOUND_DEVICE_ID) or "").strip()

            if self._osmand_mode == OSMAND_MODE_GLOBAL and not self._bound_device_id:
                errors[CONF_BOUND_DEVICE_ID] = "bound_device_id_required"

            if not errors:
                if self._osmand_mode == OSMAND_MODE_GLOBAL:
                    return await self.async_step_global_webhook()
                return await self.async_step_webhook()

        return self.async_show_form(
            step_id="osmand_mode",
            data_schema=vol.Schema({
                vol.Required(CONF_OSMAND_MODE, default=self._osmand_mode): vol.In(OSMAND_MODES),
                vol.Optional(CONF_BOUND_DEVICE_ID, default=self._bound_device_id): str,
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2b – Global webhook setup (OsmAnd global only)
    #
    # If a global webhook already exists → show its URL in a confirmation
    # step (step_id="global_webhook_existing").
    # If none exists yet → let the user configure it (step_id="global_webhook").
    # ------------------------------------------------------------------

    async def async_step_global_webhook(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """First entry point: branch based on whether global webhook exists."""
        existing_global_id = self._get_existing_global_webhook_id()
        if existing_global_id:
            return await self.async_step_global_webhook_existing(user_input)

        # No global webhook yet — let user configure it
        errors: dict[str, str] = {}
        if user_input is not None:
            use_manual = user_input.get(CONF_WEBHOOK_MANUAL, False)
            custom_id: str = (user_input.get(CONF_CUSTOM_WEBHOOK_ID) or "").strip()

            if use_manual:
                err = _validate_webhook_id(custom_id, self._async_current_entries())
                if err:
                    errors[CONF_CUSTOM_WEBHOOK_ID] = err
                else:
                    return self._build_global_entry(
                        global_webhook_id=custom_id, global_manual=True
                    )
            else:
                return self._build_global_entry(
                    global_webhook_id=secrets.token_hex(32), global_manual=False
                )

        return self.async_show_form(
            step_id="global_webhook",
            data_schema=vol.Schema({
                vol.Required(CONF_WEBHOOK_MANUAL, default=False): bool,
                vol.Optional(CONF_CUSTOM_WEBHOOK_ID, default=""): str,
            }),
            errors=errors,
            description_placeholders={"min_length": str(WEBHOOK_ID_MIN_LENGTH)},
        )

    async def async_step_global_webhook_existing(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Confirm re-use of an existing global webhook."""
        existing_global_id = self._get_existing_global_webhook_id()

        if user_input is not None:
            return self._build_global_entry(
                global_webhook_id=existing_global_id,
                global_manual=self._get_existing_global_webhook_manual(),
            )

        return self.async_show_form(
            step_id="global_webhook_existing",
            data_schema=vol.Schema({}),  # no fields — just info + confirm
            description_placeholders={
                "webhook_url": _build_webhook_url(self.hass, existing_global_id),
                "global_webhook_id": existing_global_id,
            },
        )

    # ------------------------------------------------------------------
    # Step 3 – Individual webhook (OsmAnd individual or CSV)
    # ------------------------------------------------------------------

    async def async_step_webhook(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            use_manual = user_input.get(CONF_WEBHOOK_MANUAL, False)
            custom_id: str = (user_input.get(CONF_CUSTOM_WEBHOOK_ID) or "").strip()

            if use_manual:
                err = _validate_webhook_id(custom_id, self._async_current_entries())
                if err:
                    errors[CONF_CUSTOM_WEBHOOK_ID] = err
                else:
                    return self._build_individual_entry(custom_id, manual=True)
            else:
                return self._build_individual_entry(secrets.token_hex(32), manual=False)

        return self.async_show_form(
            step_id="webhook",
            data_schema=vol.Schema({
                vol.Required(CONF_WEBHOOK_MANUAL, default=False): bool,
                vol.Optional(CONF_CUSTOM_WEBHOOK_ID, default=""): str,
            }),
            errors=errors,
            description_placeholders={"min_length": str(WEBHOOK_ID_MIN_LENGTH)},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_existing_global_webhook_id(self) -> str | None:
        """Return the global webhook_id already stored in any sibling entry."""
        for entry in self._async_current_entries():
            wid = entry.data.get(CONF_GLOBAL_WEBHOOK_ID)
            if wid:
                return wid
        return None

    def _get_existing_global_webhook_manual(self) -> bool:
        for entry in self._async_current_entries():
            if entry.data.get(CONF_GLOBAL_WEBHOOK_ID):
                return entry.data.get(CONF_GLOBAL_WEBHOOK_MANUAL, False)
        return False

    def _build_global_entry(
        self, global_webhook_id: str, global_manual: bool
    ) -> FlowResult:
        """Create a config entry for an OsmAnd global-mode device."""
        return self.async_create_entry(
            title=self._device_name,
            data={
                CONF_NAME: self._device_name,
                CONF_DEVICE_TYPE: DEVICE_TYPE_OSMAND,
                CONF_OSMAND_MODE: OSMAND_MODE_GLOBAL,
                CONF_BOUND_DEVICE_ID: self._bound_device_id,
                # Store global webhook info so it survives restarts
                CONF_GLOBAL_WEBHOOK_ID: global_webhook_id,
                CONF_GLOBAL_WEBHOOK_MANUAL: global_manual,
            },
        )

    def _build_individual_entry(self, webhook_id: str, *, manual: bool) -> FlowResult:
        """Create a config entry for an individual-webhook device."""
        data: dict = {
            CONF_NAME: self._device_name,
            CONF_DEVICE_TYPE: self._device_type,
            CONF_WEBHOOK_ID: webhook_id,
            CONF_WEBHOOK_MANUAL: manual,
        }
        if self._device_type == DEVICE_TYPE_OSMAND:
            data[CONF_OSMAND_MODE] = OSMAND_MODE_INDIVIDUAL
        return self.async_create_entry(title=self._device_name, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GpsTrackerOptionsFlow(config_entry)


# ──────────────────────────────────────────────────────────────────────
# Options Flow (reconfigure existing entry)
# ──────────────────────────────────────────────────────────────────────

class GpsTrackerOptionsFlow(config_entries.OptionsFlow):
    """Reconfigure an existing Location Receiver device."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._device_type: str = config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_OSMAND)
        self._osmand_mode: str = config_entry.data.get(CONF_OSMAND_MODE, OSMAND_MODE_INDIVIDUAL)
        self._bound_device_id: str = config_entry.data.get(CONF_BOUND_DEVICE_ID, "")
        self._new_osmand_mode: str = self._osmand_mode
        self._new_bound_device_id: str = self._bound_device_id

    # ------------------------------------------------------------------
    # Init — overview + action selector
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            action = user_input.get("action", "webhook")
            if action == "global_webhook":
                return await self.async_step_global_webhook()
            if action == "osmand_mode":
                return await self.async_step_osmand_mode()
            return await self.async_step_webhook()

        is_global = (
            self._device_type == DEVICE_TYPE_OSMAND
            and self._osmand_mode == OSMAND_MODE_GLOBAL
        )

        # Build webhook URL for display
        if is_global:
            active_wid = self._entry.data.get(CONF_GLOBAL_WEBHOOK_ID, "")
            webhook_label = "Global OsmAnd webhook"
            is_manual = self._entry.data.get(CONF_GLOBAL_WEBHOOK_MANUAL, False)
        else:
            active_wid = self._entry.data.get(CONF_WEBHOOK_ID, "")
            webhook_label = "Individual webhook"
            is_manual = self._entry.data.get(CONF_WEBHOOK_MANUAL, False)

        webhook_url = _build_webhook_url(self.hass, active_wid) if active_wid else "—"

        # Build action choices depending on device type/mode
        actions: dict[str, str] = {}
        if is_global:
            actions["global_webhook"] = "Change global OsmAnd webhook ID"
            actions["osmand_mode"] = "Change bound device ID"
        elif self._device_type == DEVICE_TYPE_OSMAND:
            actions["webhook"] = "Change webhook ID"
            actions["osmand_mode"] = "Change OsmAnd mode / bound device ID"
        else:
            actions["webhook"] = "Change webhook ID"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("action", default=list(actions.keys())[0]): vol.In(actions),
            }),
            description_placeholders={
                "webhook_url": webhook_url,
                "active_webhook_id": active_wid,
                "webhook_label": webhook_label,
                "webhook_type": "Manual" if is_manual else "Auto-generated (secure)",
                "device_type": DEVICE_TYPES.get(self._device_type, self._device_type),
                "osmand_mode": OSMAND_MODES.get(self._osmand_mode, self._osmand_mode),
                "bound_device_id": self._bound_device_id or "N/A",
            },
        )

    # ------------------------------------------------------------------
    # Change global OsmAnd webhook ID (affects all global-mode devices)
    # ------------------------------------------------------------------

    async def async_step_global_webhook(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        current_wid = self._entry.data.get(CONF_GLOBAL_WEBHOOK_ID, "")
        current_manual = self._entry.data.get(CONF_GLOBAL_WEBHOOK_MANUAL, False)

        if user_input is not None:
            use_manual = user_input.get(CONF_WEBHOOK_MANUAL, False)
            custom_id: str = (user_input.get(CONF_CUSTOM_WEBHOOK_ID) or "").strip()

            if use_manual:
                err = _validate_webhook_id(
                    custom_id,
                    self.hass.config_entries.async_entries(DOMAIN),
                    current_entry_id=self._entry.entry_id,
                )
                if err:
                    errors[CONF_CUSTOM_WEBHOOK_ID] = err
                else:
                    return self._save_global_webhook(custom_id, manual=True)
            else:
                return self._save_global_webhook(secrets.token_hex(32), manual=False)

        return self.async_show_form(
            step_id="global_webhook",
            data_schema=vol.Schema({
                vol.Required(CONF_WEBHOOK_MANUAL, default=current_manual): bool,
                vol.Optional(
                    CONF_CUSTOM_WEBHOOK_ID,
                    default=current_wid if current_manual else "",
                ): str,
            }),
            errors=errors,
            description_placeholders={
                "current_webhook_url": _build_webhook_url(self.hass, current_wid),
                "current_webhook_id": current_wid,
                "min_length": str(WEBHOOK_ID_MIN_LENGTH),
            },
        )

    # ------------------------------------------------------------------
    # Change OsmAnd mode / bound device ID
    # ------------------------------------------------------------------

    async def async_step_osmand_mode(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._new_osmand_mode = user_input[CONF_OSMAND_MODE]
            self._new_bound_device_id = (user_input.get(CONF_BOUND_DEVICE_ID) or "").strip()

            if self._new_osmand_mode == OSMAND_MODE_GLOBAL and not self._new_bound_device_id:
                errors[CONF_BOUND_DEVICE_ID] = "bound_device_id_required"

            if not errors:
                return self._save_osmand_mode()

        # Determine active webhook URL for context
        if self._osmand_mode == OSMAND_MODE_GLOBAL:
            wid = self._entry.data.get(CONF_GLOBAL_WEBHOOK_ID, "")
        else:
            wid = self._entry.data.get(CONF_WEBHOOK_ID, "")
        webhook_url = _build_webhook_url(self.hass, wid) if wid else "—"

        return self.async_show_form(
            step_id="osmand_mode",
            data_schema=vol.Schema({
                vol.Required(CONF_OSMAND_MODE, default=self._osmand_mode): vol.In(OSMAND_MODES),
                vol.Optional(CONF_BOUND_DEVICE_ID, default=self._bound_device_id): str,
            }),
            errors=errors,
            description_placeholders={
                "webhook_url": webhook_url,
                "active_webhook_id": wid,
            },
        )

    # ------------------------------------------------------------------
    # Change individual webhook ID
    # ------------------------------------------------------------------

    async def async_step_webhook(
        self, user_input: dict | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        current_wid = self._entry.data.get(CONF_WEBHOOK_ID, "")
        current_manual = self._entry.data.get(CONF_WEBHOOK_MANUAL, False)

        if user_input is not None:
            use_manual = user_input.get(CONF_WEBHOOK_MANUAL, False)
            custom_id: str = (user_input.get(CONF_CUSTOM_WEBHOOK_ID) or "").strip()

            if use_manual:
                err = _validate_webhook_id(
                    custom_id,
                    self.hass.config_entries.async_entries(DOMAIN),
                    current_entry_id=self._entry.entry_id,
                )
                if err:
                    errors[CONF_CUSTOM_WEBHOOK_ID] = err
                else:
                    return self._save_individual_webhook(custom_id, manual=True)
            else:
                return self._save_individual_webhook(secrets.token_hex(32), manual=False)

        return self.async_show_form(
            step_id="webhook",
            data_schema=vol.Schema({
                vol.Required(CONF_WEBHOOK_MANUAL, default=current_manual): bool,
                vol.Optional(
                    CONF_CUSTOM_WEBHOOK_ID,
                    default=current_wid if current_manual else "",
                ): str,
            }),
            errors=errors,
            description_placeholders={
                "current_webhook_url": _build_webhook_url(self.hass, current_wid),
                "current_webhook_id": current_wid,
                "min_length": str(WEBHOOK_ID_MIN_LENGTH),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _save_global_webhook(self, new_id: str, *, manual: bool) -> FlowResult:
        """Propagate new global webhook ID to ALL global-mode entries and reload them.

        Steps:
        1. Clear the in-memory global state flag so _ensure_global_webhook_registered
           will unregister the old webhook and register the new one on next setup.
        2. Update all global-mode entries' persistent data with the new ID.
        3. Schedule a reload for every affected sibling entry so the new webhook
           is registered immediately (the current entry is reloaded automatically
           by the OptionsFlow completion).
        """

        # Step 1 — mark global runtime state as stale so _ensure_global_webhook_registered
        # will unregister the old webhook and register the new one on next setup
        g = self.hass.data.get("ha_location_receiver", {}).get("global", {})
        if g:
            g["_webhook_registered"] = False  # force re-registration on next setup

        # Step 2 — persist new ID in every global-mode entry
        affected_entries = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if (
                entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_OSMAND
                and entry.data.get(CONF_OSMAND_MODE) == OSMAND_MODE_GLOBAL
            ):
                updated = dict(entry.data)
                updated[CONF_GLOBAL_WEBHOOK_ID] = new_id
                updated[CONF_GLOBAL_WEBHOOK_MANUAL] = manual
                self.hass.config_entries.async_update_entry(entry, data=updated)
                if entry.entry_id != self._entry.entry_id:
                    affected_entries.append(entry.entry_id)

        # Step 3 — schedule reload for sibling entries (the current entry is
        # reloaded automatically by the OptionsFlow completion)
        if affected_entries:
            async def _reload_siblings(_now=None):
                for eid in affected_entries:
                    sibling = self.hass.config_entries.async_get_entry(eid)
                    if sibling:
                        await self.hass.config_entries.async_reload(eid)

            async_call_later(self.hass, 0, _reload_siblings)

        return self.async_create_entry(title="", data={})

    def _save_osmand_mode(self) -> FlowResult:
        updated = dict(self._entry.data)
        updated[CONF_OSMAND_MODE] = self._new_osmand_mode
        updated[CONF_BOUND_DEVICE_ID] = self._new_bound_device_id
        self.hass.config_entries.async_update_entry(self._entry, data=updated)
        return self.async_create_entry(title="", data={})

    def _save_individual_webhook(self, new_id: str, *, manual: bool) -> FlowResult:
        updated = dict(self._entry.data)
        updated[CONF_WEBHOOK_ID] = new_id
        updated[CONF_WEBHOOK_MANUAL] = manual
        self.hass.config_entries.async_update_entry(self._entry, data=updated)
        return self.async_create_entry(title="", data={})
