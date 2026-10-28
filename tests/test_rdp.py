"""build_argv: the sdl-freerdp command line for a CLI connection."""

from avd_feed_connect.rdp import build_argv, set_rdp_multimon
from avd_feed_connect.rdp.launcher import build_display_args
from avd_feed_connect.rdp.resources import set_rdp_dynamic_resolution


def test_dynamic_display_settings_in_freerdp(tmp_path):
    import ctypes
    import os
    import pytest

    if not os.environ.get("AVD_TEST_FREERDP"):
        pytest.skip("Set AVD_TEST_FREERDP=1 inside the bundled FreeRDP runtime")
    library = ctypes.CDLL("libfreerdp-client3.so.3")
    library.freerdp_settings_new.argtypes = [ctypes.c_uint32]
    library.freerdp_settings_new.restype = ctypes.c_void_p
    library.freerdp_settings_free.argtypes = [ctypes.c_void_p]
    library.freerdp_settings_free.restype = None
    library.freerdp_settings_get_bool.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    library.freerdp_settings_get_bool.restype = ctypes.c_int
    library.freerdp_settings_get_name_for_key.argtypes = [ctypes.c_size_t]
    library.freerdp_settings_get_name_for_key.restype = ctypes.c_char_p
    parse = library.freerdp_client_settings_parse_command_line
    parse.argtypes = [ctypes.c_void_p, ctypes.c_int,
                      ctypes.POINTER(ctypes.c_char_p), ctypes.c_int]
    parse.restype = ctypes.c_int
    path = _rdp(tmp_path, "smart sizing:i:1\nscreen mode id:i:2\n"
                "dynamic resolution:i:1\nuse multimon:i:1\n")
    args = ["sdl-freerdp", path] + build_display_args(path, "/dynamic-resolution", True)
    argv = (ctypes.c_char_p * len(args))(*(arg.encode() for arg in args))
    settings = library.freerdp_settings_new(0)
    assert settings
    try:
        assert parse(settings, len(args), argv, False) == 0
        expected = {388: ("FreeRDP_UseMultimon", False),
                    1537: ("FreeRDP_Fullscreen", False),
                    1551: ("FreeRDP_SmartSizing", False),
                    1558: ("FreeRDP_DynamicResolutionUpdate", True),
                    5185: ("FreeRDP_SupportDisplayControl", True)}
        for key, (name, value) in expected.items():
            assert library.freerdp_settings_get_name_for_key(key).decode() == name
            assert bool(library.freerdp_settings_get_bool(settings, key)) == value, name
    finally:
        library.freerdp_settings_free(settings)


def test_dynamic_resolution_clears_conflicting_feed_settings(tmp_path):
    path = _rdp(tmp_path, "smart sizing:i:1\nscreen mode id:i:2\nusername:s:u\n")
    set_rdp_dynamic_resolution(path)
    lines = open(path).read().splitlines()
    assert "smart sizing:i:0" in lines
    assert "smart sizing:i:1" not in lines
    assert "screen mode id:i:1" in lines
    assert "screen mode id:i:2" not in lines
    assert "username:s:u" in lines


def test_dynamic_resolution_adds_missing_properties(tmp_path):
    path = _rdp(tmp_path, "username:s:u\n")
    set_rdp_dynamic_resolution(path)
    set_rdp_dynamic_resolution(path)
    lines = open(path).read().splitlines()
    assert lines.count("smart sizing:i:0") == 1
    assert lines.count("screen mode id:i:1") == 1


def test_dynamic_display_args_override_conflicting_modes(tmp_path):
    for flag in ("/dynamic-resolution", "+dynamic-resolution"):
        for extra in (f"/smart-sizing:1280x720 /f /multimon {flag}",
                      f"{flag} /smart-sizing +f +multimon"):
            path = _rdp(tmp_path, "smart sizing:i:1\nuse multimon:i:1\n")
            args = build_display_args(path, extra + ' /drive:home,"/my folder"', True)
            assert args == ["-multimon", flag, "/drive:home,/my folder"]
            assert "smart sizing:i:0" in open(path).read().splitlines()
            assert "use multimon:i:0" in open(path).read().splitlines()


def test_non_dynamic_display_preserves_smart_sizing(tmp_path):
    path = _rdp(tmp_path, "smart sizing:i:1\n")
    args = build_display_args(path, "/smart-sizing", True)
    assert args == ["/f", "/multimon", "/smart-sizing"]
    assert "smart sizing:i:1" in open(path).read().splitlines()


def test_dynamic_flag_must_be_an_enabled_option(tmp_path):
    for extra in ("-dynamic-resolution", "/dynamic-resolution -dynamic-resolution",
                  "/drive:dynamic-resolution,/tmp"):
        path = _rdp(tmp_path, "username:s:u\n")
        args = build_display_args(path, extra, False)
        assert args[0] == "/f"
        assert "smart sizing:i:0" not in open(path).read().splitlines()


def test_explicit_single_monitor_updates_feed(tmp_path):
    path = _rdp(tmp_path, "use multimon:i:1\n")
    args = build_display_args(path, "-multimon", True)
    assert "/multimon" not in args
    assert "use multimon:i:0" in open(path).read().splitlines()


def test_malformed_display_args_are_reported(tmp_path):
    path = _rdp(tmp_path, "username:s:u\n")
    try:
        build_display_args(path, '/drive:home,"unterminated', False)
    except ValueError:
        return
    raise AssertionError("Malformed flags must not be silently ignored")


def test_argv_core_flags(tmp_path):
    argv = build_argv("/app/bin/sdl-freerdp", _rdp(tmp_path, "enablerdsaadauth:i:1\n"), "")
    assert argv[0] == "/app/bin/sdl-freerdp"
    assert argv[1] == _rdp(tmp_path, "enablerdsaadauth:i:1\n")
    assert "/gateway:type:arm" in argv
    assert "/sec:aad" in argv
    assert "/f" in argv
    assert "-multimon" in argv


def test_argv_includes_user_when_upn_set(tmp_path):
    argv = build_argv("sdl-freerdp", _rdp(tmp_path, "enablerdsaadauth:i:1\n"), "sam@contoso.com")
    assert "/u:sam@contoso.com" in argv


def test_argv_omits_user_when_no_upn(tmp_path):
    argv = build_argv("sdl-freerdp", _rdp(tmp_path, "enablerdsaadauth:i:1\n"), "")
    assert not any(a.startswith("/u:") for a in argv)


def test_argv_verifies_cert_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AVD_CERT", raising=False)
    argv = build_argv("sdl-freerdp", _rdp(tmp_path, "enablerdsaadauth:i:1\n"), "")
    assert "/cert:tofu" in argv
    assert "/cert:ignore" not in argv


def test_argv_cert_ignore_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("AVD_CERT", "ignore")
    argv = build_argv("sdl-freerdp", _rdp(tmp_path, "enablerdsaadauth:i:1\n"), "")
    assert "/cert:ignore" in argv
    assert "/cert:tofu" not in argv


def _rdp(tmp_path, body):
    p = tmp_path / "x.rdp"
    p.write_text(body)
    return str(p)


def test_multimon_disable_rewrites_existing_property(tmp_path):
    # AVD feed .rdp ships "use multimon:i:1"; Single monitor must flip it to 0.
    path = _rdp(tmp_path, "full address:s:host\nuse multimon:i:1\nusername:s:u\n")
    set_rdp_multimon(path, False)
    lines = open(path).read().splitlines()
    assert "use multimon:i:0" in lines
    assert "use multimon:i:1" not in lines
    assert "full address:s:host" in lines  # other lines untouched


def test_multimon_enable_sets_one(tmp_path):
    path = _rdp(tmp_path, "use multimon:i:0\n")
    set_rdp_multimon(path, True)
    assert "use multimon:i:1" in open(path).read().splitlines()


def test_multimon_appends_when_absent(tmp_path):
    path = _rdp(tmp_path, "full address:s:host\n")
    set_rdp_multimon(path, False)
    assert "use multimon:i:0" in open(path).read().splitlines()


def test_multimon_missing_file_is_noop(tmp_path):
    set_rdp_multimon(str(tmp_path / "nope.rdp"), True)  # must not raise


def test_client_hotkeys_off_preserves_other_keys(tmp_path):
    import json
    from avd_feed_connect.rdp.launcher import apply_client_hotkeys

    path = tmp_path / "freerdp" / "sdl-freerdp.json"
    path.parent.mkdir()
    path.write_text('{"SDL_Grab": "SDL_SCANCODE_K"}')
    assert apply_client_hotkeys("off", str(path))
    assert json.loads(path.read_text()) == {
        "SDL_Grab": "SDL_SCANCODE_K", "SDL_KeyModMask": ["KMOD_NONE"]}
    assert apply_client_hotkeys("on", str(path))
    assert json.loads(path.read_text())["SDL_KeyModMask"] == ["KMOD_RSHIFT"]


def test_client_hotkeys_auto_and_bad_config_left_alone(tmp_path):
    from avd_feed_connect.rdp.launcher import apply_client_hotkeys

    path = tmp_path / "sdl-freerdp.json"
    assert not apply_client_hotkeys("auto", str(path))
    assert not path.exists()
    path.write_text("{not json")
    assert not apply_client_hotkeys("off", str(path))
    assert path.read_text() == "{not json"


def test_account_downloads_keep_same_title_resources_separate(tmp_path, monkeypatch):
    from avd_feed_connect.rdp import resources
    monkeypatch.setattr(resources.http, 'get', lambda url, token: (200, {}, 'username:s:' + token))
    a = resources.download_rdp('a', {'id': 'one', 'title': 'Desktop', 'rdp_url': 'https://example.microsoft.com/one'}, str(tmp_path / 'a'))
    b = resources.download_rdp('b', {'id': 'two', 'title': 'Desktop', 'rdp_url': 'https://example.microsoft.com/two'}, str(tmp_path / 'a'))
    c = resources.download_rdp('c', {'id': 'one', 'title': 'Desktop', 'rdp_url': 'https://example.microsoft.com/one'}, str(tmp_path / 'b'))
    assert len({a, b, c}) == 3
    assert open(a).read() == 'username:s:a'
    assert open(b).read() == 'username:s:b'
    assert open(c).read() == 'username:s:c'


def test_cli_repeated_connections_preserve_each_log(tmp_path, monkeypatch):
    from avd_feed_connect import config
    from avd_feed_connect.rdp.launcher import RdpLauncher

    output = tmp_path / 'feed'
    output.mkdir()
    legacy = output / 'feed-last-run.log'
    legacy.write_text('previous session\n')
    monkeypatch.setattr(config, 'OUT', str(output))
    client = tmp_path / 'fake-freerdp'
    client.write_text('#!/usr/bin/python3\n'
                      'import pathlib, sys\n'
                      'print(pathlib.Path(sys.argv[1]).read_text(), end="", flush=True)\n'
                      'print("diagnostic stderr", file=sys.stderr)\n')
    client.chmod(0o755)
    rdp = tmp_path / 'desktop.rdp'
    launcher = RdpLauncher(sdl=str(client), sdl_libs='')
    rdp.write_text('first connection\n')
    assert launcher.launch(str(rdp)) == 0
    first = {p: p.read_bytes() for p in output.glob('*.log')}
    rdp.write_text('second connection\n')
    assert launcher.launch(str(rdp)) == 0

    assert len(list(output.glob('*.log'))) == 3
    assert all(p.read_bytes() == content for p, content in first.items())
    assert legacy.read_text() == 'previous session\n'
    sessions = [p.read_text() for p in output.glob('feed_*.log')]
    assert sorted(sessions) == ['first connection\ndiagnostic stderr\n',
                                'second connection\ndiagnostic stderr\n']


def test_log_paths_remain_unique_with_identical_timestamps(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from pathlib import Path
    from avd_feed_connect.rdp import launcher

    instant = datetime(2026, 10, 20, 19, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(launcher, 'datetime', SimpleNamespace(now=lambda tz: instant))
    paths = [Path(launcher.session_log_path(tmp_path, 'session_desktop'))
             for _ in range(20)]
    assert len(set(paths)) == 20
    assert all(p.parent == tmp_path for p in paths)
    assert all(p.name.startswith('session_desktop_20261020T193000.000000Z_')
               and p.suffix == '.log' for p in paths)


def test_custom_hotkeys_write_all_actions_and_preserve_other_preferences(tmp_path):
    import json
    from avd_feed_connect.rdp.launcher import apply_client_hotkeys
    path = tmp_path / 'sdl-freerdp.json'
    path.write_text('{"unrelated": 42}')
    custom = {'modifiers': ['KMOD_RCTRL', 'KMOD_RSHIFT'], 'bindings': {
        'SDL_Disconnect': 'SDL_SCANCODE_F12', 'SDL_Minimize': 'SDL_SCANCODE_M',
        'SDL_Fullscreen': 'SDL_SCANCODE_RETURN', 'SDL_Resizeable': 'SDL_SCANCODE_R',
        'SDL_Grab': 'SDL_SCANCODE_G'}}
    assert apply_client_hotkeys(custom, str(path))
    data = json.loads(path.read_text())
    assert data['SDL_KeyModMask'] == ['KMOD_RCTRL', 'KMOD_RSHIFT']
    assert data['SDL_Disconnect'] == 'SDL_SCANCODE_F12'
    assert data['unrelated'] == 42
    assert apply_client_hotkeys('on', str(path))
    assert json.loads(path.read_text())['SDL_Disconnect'] == 'SDL_SCANCODE_F12'


def test_invalid_custom_hotkeys_do_not_modify_config(tmp_path):
    from avd_feed_connect.rdp.launcher import apply_client_hotkeys
    path = tmp_path / 'sdl-freerdp.json'
    path.write_text('{}')
    for value in ({'modifiers': [], 'bindings': {}},
                  {'modifiers': ['INVALID'], 'bindings': {}},
                  {'modifiers': ['KMOD_RSHIFT'], 'bindings': {'SDL_Disconnect': 'INVALID'}}):
        assert not apply_client_hotkeys(value, str(path))
        assert path.read_text() == '{}'


def test_restore_hotkeys_removes_only_managed_overrides(tmp_path):
    import json
    from avd_feed_connect.rdp.launcher import apply_client_hotkeys, HOTKEY_DEFAULTS
    path = tmp_path / 'sdl.json'
    path.write_text(json.dumps(dict(HOTKEY_DEFAULTS, SDL_KeyModMask=['KMOD_RCTRL'], unrelated=42)))
    assert apply_client_hotkeys('defaults', str(path))
    assert json.loads(path.read_text()) == {'unrelated': 42}


def test_custom_hotkeys_preserve_unreadable_and_non_object_files(tmp_path):
    from avd_feed_connect.rdp.launcher import apply_client_hotkeys, HOTKEY_DEFAULTS
    path = tmp_path / 'sdl.json'
    custom = {'modifiers': ['KMOD_RCTRL'], 'bindings': dict(HOTKEY_DEFAULTS)}
    for content in ('not JSON', '[]', 'null'):
        path.write_text(content)
        assert not apply_client_hotkeys(custom, str(path))
        assert path.read_text() == content


def test_custom_hotkeys_reject_duplicate_unknown_and_malformed_keys(tmp_path):
    from avd_feed_connect.rdp.launcher import apply_client_hotkeys, HOTKEY_DEFAULTS
    path = tmp_path / 'sdl.json'
    path.write_text('{"keep": true}')
    for bindings in (dict(HOTKEY_DEFAULTS, SDL_Disconnect='SDL_SCANCODE_M'),
                     dict(HOTKEY_DEFAULTS, SDL_Disconnect='SDL_SCANCODE_UNKNOWN'),
                     dict(HOTKEY_DEFAULTS, SDL_Disconnect=[])):
        assert not apply_client_hotkeys({'modifiers': ['KMOD_RSHIFT'], 'bindings': bindings}, str(path))
        assert path.read_text() == '{"keep": true}'
    assert not apply_client_hotkeys({'modifiers': [{}], 'bindings': dict(HOTKEY_DEFAULTS)}, str(path))


def test_local_resource_desktop(tmp_path):
    from avd_feed_connect.rdp.resources import local_resource
    p = tmp_path / "Army Desktop.rdp"
    p.write_text("full address:s:host.wvd.azure.us\nscreen mode id:i:2\n")
    res = local_resource(str(p))
    assert res["type"] == "Desktop"
    assert res["title"] == "Army Desktop"          # friendly filename, not the host
    assert res["tenant"]                            # non-empty label
    assert res["id"].startswith("local-")


def test_local_resource_remoteapp_uses_app_name(tmp_path):
    from avd_feed_connect.rdp.resources import local_resource
    p = tmp_path / "vc.rdp"
    p.write_text("remoteapplicationmode:i:1\nremoteapplicationname:s:VisualCron\n"
                 "full address:s:host\n")
    res = local_resource(str(p))
    assert res["type"] == "RemoteApp"
    assert res["title"] == "VisualCron"


def test_local_resource_id_stable_per_path(tmp_path):
    from avd_feed_connect.rdp.resources import local_resource
    p = tmp_path / "d.rdp"
    p.write_text("full address:s:h\n")
    assert local_resource(str(p))["id"] == local_resource(str(p))["id"]


def test_rdp_property_reads_value_and_missing(tmp_path):
    from avd_feed_connect.rdp.resources import rdp_property
    p = tmp_path / "d.rdp"
    p.write_text("full address:s:host.example\nuse multimon:i:1\n")
    assert rdp_property(str(p), "full address") == "host.example"
    assert rdp_property(str(p), "USE MULTIMON") == "1"      # case-insensitive
    assert rdp_property(str(p), "missing") is None
    assert rdp_property(str(tmp_path / "nope.rdp"), "full address") is None
