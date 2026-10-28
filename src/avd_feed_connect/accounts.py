"""Saved work-account identities and their private storage locations."""

import base64
import json
import os
import re
import shutil
import sys
import uuid

from .i18n import _ as tr
from . import config
from .auth.tokens import TokenStore
from .client import AvdClient


# Synthetic account that holds user-opened .rdp files (issue #17). It has no
# feed and no token, so it never gets a 32-hex id from a token's identity the
# way real accounts do; this fixed, path-safe id is allowed through below.
LOCAL_PROFILE_ID = 'local-rdp-files'


def profile_dir(root, profile_id):
    if profile_id != LOCAL_PROFILE_ID and (
            not isinstance(profile_id, str) or not re.fullmatch(r'[0-9a-f]{32}', profile_id)):
        raise ValueError(tr('Invalid account profile ID'))
    return os.path.join(root, 'accounts', profile_id)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + '.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as destination:
        json.dump(data, destination)
    os.replace(temporary, path)


def claims_from_token(tok):
    """Read identity metadata from a Microsoft token response, not verify JWTs."""
    try:
        payload = tok['id_token'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if not all(isinstance(claims.get(k), str) and claims[k].strip() for k in ('tid', 'oid')):
            raise ValueError()
        upn = next((claims[k] for k in ('preferred_username', 'upn', 'email')
                    if isinstance(claims.get(k), str) and claims[k]), '')
        return {'tid': claims['tid'], 'oid': claims['oid'], 'upn': upn}
    except (KeyError, IndexError, ValueError, TypeError, AttributeError) as error:
        raise ValueError(tr('Microsoft did not return a stable work-account identity. Please sign in again.')) from error


class ProfileRegistry:
    def __init__(self, root=None):
        self.root = root or os.path.dirname(config.CACHE)
        self.path = os.path.join(self.root, 'accounts.json')
        self.profiles = []
        self.selected_id = None
        self.migration_pending = False
        self._load_error = None
        try:
            with open(self.path) as source:
                data = json.load(source)
            if not isinstance(data, dict) or not isinstance(data.get('profiles'), list):
                raise ValueError(tr('Invalid saved account registry'))
            profiles = data['profiles']
            for profile in profiles:
                if not isinstance(profile, dict):
                    raise ValueError(tr('Invalid saved account'))
                profile_dir(self.root, profile['id'])
                if not all(isinstance(profile.get(k), str) for k in ('tid', 'oid', 'upn')):
                    raise ValueError(tr('Invalid saved account'))
            selected_id = data.get('selected_id')
            if not isinstance(selected_id, str) or selected_id not in {p['id'] for p in profiles}:
                selected_id = profiles[0]['id'] if profiles else None
            self.migration_pending = bool(data.get('migration_pending'))
            self.profiles = profiles
            self.selected_id = selected_id
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError):
            # Preserve the original bytes before allowing a fresh registry.
            backup = self.path + '.invalid-' + uuid.uuid4().hex
            try:
                os.replace(self.path, backup)
                print(f'Invalid account registry preserved at {backup}', file=sys.stderr)
            except OSError as backup_error:
                self._load_error = backup_error
                print('Could not preserve the invalid account registry; saving is disabled.', file=sys.stderr)
        except OSError as error:
            self._load_error = error
            print('Could not read the account registry; saving is disabled.', file=sys.stderr)

    def save(self):
        if self._load_error is not None:
            raise OSError(tr('Cannot overwrite an unreadable or unpreserved account registry')) from self._load_error
        write_json(self.path, {'profiles': self.profiles, 'selected_id': self.selected_id,
                               'migration_pending': self.migration_pending})

    def select(self, profile_id):
        if profile_id not in {p['id'] for p in self.profiles}:
            raise ValueError(tr('Unknown account'))
        self.selected_id = profile_id
        self.save()

    def upsert(self, claims, profile_id=None):
        if not claims.get('tid') or not claims.get('oid'):
            raise ValueError(tr('Missing account identity'))
        profile = next((p for p in self.profiles
                        if (p['tid'], p['oid']) == (claims['tid'], claims['oid'])), None)
        if profile is None:
            profile = {'id': profile_id or uuid.uuid4().hex}
            profile_dir(self.root, profile['id'])
            self.profiles.append(profile)
        profile.update({k: claims[k] for k in ('tid', 'oid', 'upn')})
        self.selected_id = profile['id']
        self.save()
        return profile

    def rename(self, profile_id, name):
        profile = next(p for p in self.profiles if p['id'] == profile_id)
        name = name.strip()
        if name:
            profile['alias'] = name
        else:
            profile.pop('alias', None)
        self.save()

    def remove(self, profile_id):
        self.profiles = [p for p in self.profiles if p['id'] != profile_id]
        if self.selected_id == profile_id:
            self.selected_id = self.profiles[0]['id'] if self.profiles else None
        self.save()

    def client(self, profile_id=None):
        profile_id = profile_id or self.selected_id
        if profile_id is None:
            return AvdClient()
        directory = profile_dir(self.root, profile_id)
        store = TokenStore(os.path.join(directory, 'token-cache.json'), account_id=profile_id)
        client = AvdClient(store, os.path.join(directory, 'feed'))
        profile = next((p for p in self.profiles if p['id'] == profile_id), None)
        if profile:
            client.upn = profile['upn']
            if profile['tid'] and profile['oid']:
                client.oauth.account_identity = (profile['tid'], profile['oid'])
        return client

    def migrate_legacy(self, token_store):
        """Copy, publish, then clean up; retry the same profile after a crash."""
        profile_id = uuid.uuid5(uuid.NAMESPACE_URL, self.root + '/legacy-account').hex
        existing = next((p for p in self.profiles if p['id'] == profile_id), None)
        if self.profiles and not existing:
            return
        record = token_store.load()
        if not record and not (existing and self.migration_pending):
            return
        directory = profile_dir(self.root, profile_id)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        destination = self.client(profile_id).tokens
        if not destination.has() and record:
            destination.save(record['refresh_token'])
        if not destination.load():
            raise OSError(tr('Could not verify migrated credentials'))
        upn = ''
        for name in ('workspaces.json', 'icons', 'webview', 'feed'):
            source = os.path.join(self.root, name)
            target = os.path.join(directory, name)
            if os.path.exists(source):
                if os.path.isdir(source):
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, target)
            if name == 'workspaces.json' and os.path.exists(target):
                with open(target) as stream:
                    upn = json.load(stream).get('upn', '')
        settings_path = os.path.join(self.root, 'settings.json')
        try:
            with open(settings_path) as stream:
                settings = json.load(stream)
        except FileNotFoundError:
            settings = {}
        global_settings = {k: v for k, v in settings.items() if k.startswith('_')}
        resources = {k: v for k, v in settings.items() if not k.startswith('_')}
        if resources:
            write_json(os.path.join(directory, 'settings.json'), resources)
        if not existing:
            self.profiles.append({'id': profile_id, 'tid': '', 'oid': '', 'upn': upn})
            self.selected_id = profile_id
        self.migration_pending = True
        self.save()
        token_store.clear()
        write_json(settings_path, global_settings)
        for name in ('workspaces.json', 'icons', 'webview', 'feed'):
            source = os.path.join(self.root, name)
            if os.path.isdir(source):
                shutil.rmtree(source)
            elif os.path.exists(source):
                os.remove(source)
        self.migration_pending = False
        self.save()
