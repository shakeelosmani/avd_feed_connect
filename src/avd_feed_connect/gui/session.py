"""Account-owned state; independent of GTK so lifecycle races are testable."""

import json
import os
import threading
import time

from ..i18n import _ as tr
from ..accounts import claims_from_token, write_json
from . import storage


class AccountSession:
    def __init__(self, profile, client, directory):
        self.profile = profile
        self.client = client
        self.directory = directory
        self.token = None
        self.deadline = 0.0
        self.refresh_source = 0
        self.refresh_lock = threading.RLock()
        self._refresh_serial = threading.Lock()
        self.web_session = None
        self.resources, _ = storage.load_ws_cache(directory)
        self.tiles = {}
        self.pending_launch = None
        self.removed = False
        try:
            with open(os.path.join(directory, 'settings.json')) as source:
                self.resource_settings = json.load(source)
        except (OSError, ValueError):
            self.resource_settings = {}
        try:
            with open(os.path.join(directory, 'auth-modes.json')) as source:
                self.auth_modes = json.load(source)
            if not isinstance(self.auth_modes, dict):
                self.auth_modes = {}
        except (OSError, ValueError):
            self.auth_modes = {}

    @property
    def id(self):
        return self.profile['id']

    @property
    def local(self):
        """True for the synthetic account that holds user-opened .rdp files
        (no feed, no token, no sign-in); see AvdApp._local_session (issue #17)."""
        return bool(self.profile.get('local'))

    @property
    def display_name(self):
        if self.profile.get('alias'):
            return self.profile['alias']
        # AVD TenantDisplayName can be a workspace/Cloud PC name, not an
        # organization name. Only the user-supplied alias names the company.
        domain = self.client.upn.partition('@')[2]
        return domain or self.client.upn or tr("Saved account")

    def apply_token(self, tok):
        with self.refresh_lock:
            if self.removed:
                return False
            if tok.get('id_token'):
                claims = claims_from_token(tok)
                if self.profile['tid'] and (claims['tid'], claims['oid']) != (self.profile['tid'], self.profile['oid']):
                    self.client.oauth.last_refresh_error = tr('Please sign in with the selected work account.')
                    return False
                self.profile.update(claims)
                self.client.upn = claims['upn']
            self.token = tok['access_token']
            self.deadline = time.monotonic() + int(tok.get('expires_in', 3600))
            if tok.get('refresh_token'):
                self.client.save_refresh_token(tok['refresh_token'])
            return True

    def refresh(self):
        with self._refresh_serial:
            with self.refresh_lock:
                if self.removed:
                    return False
                if self.token and time.monotonic() < self.deadline - 300:
                    return True
                record = self.client.load_token_record()
            if not record:
                return False
            try:
                tok = self.client.oauth._refresh(record['refresh_token'])
                return bool(tok and self.apply_token(tok))
            except (OSError, ValueError) as error:
                self.client.oauth.last_refresh_error = str(error)
                return False

    def save_resources(self, resources):
        with self.refresh_lock:
            if self.removed:
                return False
            self.resources = resources
            storage.save_ws_cache(resources, self.client.upn, self.directory)
            return True

    def save_preferences(self):
        with self.refresh_lock:
            if not self.removed:
                write_json(os.path.join(self.directory, 'settings.json'), self.resource_settings)

    def remember_security(self, resource_id, mode, fingerprint):
        """Only call after successful logon; never retain passwords or tokens."""
        with self.refresh_lock:
            if not self.removed and mode in ('aad', 'nla'):
                self.auth_modes[resource_id] = {'mode': mode, 'fingerprint': fingerprint}
                write_json(os.path.join(self.directory, 'auth-modes.json'), self.auth_modes)

    def close(self):
        with self.refresh_lock:
            self.removed = True
            self.pending_launch = None
            self.token = None
            self.client.clear_token_cache()
