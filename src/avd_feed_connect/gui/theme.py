"""GTK4 CSS theme for the app.

GTK CSS is not web CSS (no var()/transform/::before): the palette is defined
twice with @define-color — a light set and a dark set — and the matching set
is prepended to the base stylesheet based on the desktop's dark preference.
"""

# ---- theme / styling -------------------------------------------------------
# GTK CSS is not web CSS: no var(), no transform, no ::before. We define the
# palette with @define-color (a light set and a dark set) and pick which to load
# based on the desktop's dark preference, so the look is consistent across
# distros/themes instead of inheriting whatever GTK theme is active.
PALETTE_LIGHT = """
@define-color avd_bg #F4F6F9;
@define-color avd_surface #FFFFFF;
@define-color avd_surface2 #F1F4F8;
@define-color avd_border #E3E8EF;
@define-color avd_border_strong #D3DAE3;
@define-color avd_ink #1A2230;
@define-color avd_muted #66707E;
@define-color avd_faint #98A2B3;
@define-color avd_accent #0E7CF4;
@define-color avd_accent_ink #0B63C4;
@define-color avd_ok #2FB86B;
@define-color avd_warn #B87E00;
"""
PALETTE_DARK = """
@define-color avd_bg #161A21;
@define-color avd_surface #212630;
@define-color avd_surface2 #2A303B;
@define-color avd_border #353D49;
@define-color avd_border_strong #48525F;
@define-color avd_ink #F2F5FA;
@define-color avd_muted #AEB8C6;
@define-color avd_faint #7B8593;
@define-color avd_accent #57A0FF;
@define-color avd_accent_ink #8ABAFF;
@define-color avd_ok #4FD08D;
@define-color avd_warn #F2B838;
"""
CSS_BASE = """
window.avd, .avd-page { background:@avd_bg; }
.avd-page { background:@avd_bg; }

headerbar.avd-header {
  background:linear-gradient(to bottom, @avd_surface, @avd_surface2);
  border-bottom:1px solid @avd_border; box-shadow:none; min-height:52px;
  padding:6px 8px;
}
/* window controls (min/max/close) — keep them clearly visible in dark mode */
headerbar.avd-header windowcontrols button,
headerbar.avd-header .titlebutton { color:@avd_muted; background:none;
  box-shadow:none; min-width:26px; min-height:26px; }
headerbar.avd-header windowcontrols button:hover,
headerbar.avd-header .titlebutton:hover { color:@avd_ink; background:alpha(@avd_ink,0.08); }
.mark { min-width:34px; min-height:34px; border-radius:10px; color:#ffffff;
  background:linear-gradient(145deg,#2D8BFF,#0E63D6);
  box-shadow:0 3px 8px -2px alpha(#0E7CF4,0.55); }
.brand-title { font-weight:800; font-size:15px; color:@avd_ink; }
.brand-sub { font-size:11px; color:@avd_faint; }
button.iconbtn, menubutton.iconbtn > button { border-radius:9px; color:@avd_muted; background:none;
  border:1px solid transparent; min-width:34px; min-height:34px; box-shadow:none; padding:0; }
button.iconbtn:hover, menubutton.iconbtn > button:hover { background:@avd_bg; color:@avd_ink; border-color:@avd_border; }
/* the class sits on the menubutton; style its inner button (else it keeps the
   theme's default light background and the email text vanishes in dark mode) */
menubutton.acct-btn { background:none; box-shadow:none; }
menubutton.acct-btn > button { border-radius:20px; border:1px solid @avd_border;
  background:@avd_bg; color:@avd_ink; padding:2px 10px 2px 3px; box-shadow:none; min-height:32px; }
menubutton.acct-btn > button:hover { border-color:@avd_border_strong; background:@avd_bg; }
.avatar { min-width:26px; min-height:26px; border-radius:20px;
  background:linear-gradient(145deg,#5B6BFF,#7A3BE0); color:#ffffff;
  font-weight:800; font-size:11px; }
.acct-who { font-weight:600; font-size:12px; color:@avd_ink; }
.caret { color:@avd_faint; }

.view-modes button { background:@avd_surface2; color:@avd_ink;
  border:1px solid @avd_border; box-shadow:none; min-height:36px; }
.view-modes button:checked { background:alpha(@avd_accent,0.18);
  color:@avd_accent_ink; border-color:@avd_accent; }
.view-modes button:hover { border-color:@avd_accent; }
.section-h { font-weight:800; font-size:11px; letter-spacing:1px; color:@avd_muted; }
.count { font-size:12px; color:@avd_faint; }

flowbox, flowboxchild { background:none; padding:0; border:none; box-shadow:none; }
flowboxchild:selected, flowboxchild:focus, flowboxchild:active { background:none; }

.tile { background:@avd_surface; border:1px solid @avd_border; border-radius:15px;
  padding:15px; box-shadow:0 1px 2px alpha(#121C2E,0.05);
  transition:border-color 150ms, box-shadow 150ms, background 150ms; }
.tile:hover { border-color:@avd_accent;
  box-shadow:0 10px 24px -14px alpha(@avd_accent,0.55), 0 2px 6px -2px alpha(#121C2E,0.12); }
.tile.tile-connected { border-color:alpha(@avd_ok,0.55); }
flowboxchild:focus-visible .tile { outline:2px solid @avd_accent; outline-offset:2px; }

.ic { min-width:50px; min-height:50px; border-radius:13px; background:@avd_surface2; }
.ic.desktop { background:alpha(@avd_accent,0.13); }
.ic.desktop image { color:@avd_accent; }
.ic.app { background:alpha(@avd_warn,0.15); }
.ic.app image { color:@avd_warn; }

.tname { font-weight:800; font-size:14px; color:@avd_ink; }
.chiplabel { font-size:10px; font-weight:700; letter-spacing:0.7px; color:@avd_muted; }
.chipdot { min-width:6px; min-height:6px; border-radius:6px; background:@avd_accent; }
.chip-app .chipdot { background:@avd_warn; }

.status-pill { font-size:9px; font-weight:800; letter-spacing:0.5px; padding:3px 9px;
  border-radius:20px; background:alpha(@avd_ok,0.15); color:@avd_ok; }
.status-pill.pill-connecting { background:alpha(@avd_warn,0.18); color:@avd_warn; }

.foot { background:@avd_surface; border-top:1px solid @avd_border; padding:8px 16px; }
.status { color:@avd_muted; font-size:12px; }
.ver { color:@avd_faint; font-size:11px; }
.livedot { min-width:7px; min-height:7px; border-radius:7px; background:@avd_ok; }

.connect-card { background:@avd_surface; border:1px solid @avd_border; border-radius:18px;
  padding:26px 34px; box-shadow:0 20px 48px -14px alpha(#121C2E,0.4); }
.connect-label { font-weight:700; font-size:14px; color:@avd_ink; }
.connect-sub { font-size:12px; color:@avd_muted; }

.toast { background:@avd_surface; border:1px solid @avd_border; border-radius:14px;
  padding:10px 10px 10px 16px; box-shadow:0 16px 40px -16px alpha(#121C2E,0.5); }
.toast-label { font-size:13px; color:@avd_ink; }

.bigmark { min-width:66px; min-height:66px; border-radius:19px; color:#ffffff;
  background:linear-gradient(145deg,#2D8BFF,#0E63D6);
  box-shadow:0 12px 30px -10px alpha(#0E7CF4,0.65); }
.hero-title { font-weight:800; font-size:22px; color:@avd_ink; }
.hero-sub { font-size:14px; color:@avd_muted; }
.hero-note { font-size:11px; color:@avd_faint; }
button.msbtn { background:@avd_accent; color:#ffffff; font-weight:700; font-size:14px;
  border-radius:11px; padding:11px 20px; border:none;
  box-shadow:0 8px 18px -6px alpha(@avd_accent,0.6); }
button.msbtn:hover { background:@avd_accent_ink; }

window.avd-settings { background:@avd_surface; color:@avd_ink; }
window.avd-settings headerbar { background:@avd_surface2; color:@avd_ink;
  border-color:@avd_border; }
window.avd-settings label { color:inherit; }
window.avd-settings button, window.avd-settings entry {
  background:@avd_bg; color:@avd_ink; border-color:@avd_border_strong; box-shadow:none; }
window.avd-settings button:hover { background:@avd_surface2; }
window.avd-settings button.suggested-action { background:@avd_accent; color:#ffffff; }
window.avd-settings entry > text > placeholder { color:@avd_faint; }
window.avd-settings popover > contents { background:@avd_surface; color:@avd_ink;
  border-color:@avd_border; }
window.avd-settings listview { background:@avd_surface; color:@avd_ink; }
window.avd-settings listview row:hover { background:@avd_surface2; }
window.avd-settings listview row:selected { background:@avd_accent; color:#ffffff; }
.settings-title { font-weight:800; font-size:17px; color:@avd_ink; }
.settings-sub { font-size:12px; color:@avd_muted; }
.field-label { font-weight:800; font-size:11px; letter-spacing:0.6px; color:@avd_muted; }
/* Popovers have their own surfaces; explicitly share the app palette. */
popover.avd-menu > contents { background:@avd_surface; color:@avd_ink;
  border:1px solid @avd_border; }
popover.avd-menu arrow { background:@avd_surface; border-color:@avd_border; }
popover.avd-menu button.flat { color:@avd_ink; background:none; box-shadow:none; }
popover.avd-menu button.flat:hover { background:@avd_surface2; }
popover.avd-menu button.flat:disabled { color:@avd_faint; }
popover.avd-menu checkbutton { color:@avd_muted; }
popover.avd-menu checkbutton:disabled { color:@avd_faint; }
popover.avd-menu checkbutton check { background:@avd_bg; border-color:@avd_border_strong; }
popover.avd-menu checkbutton check:checked { background:@avd_accent; color:#ffffff; }
popover.avd-menu separator { background:@avd_border; }
.account-name { font-weight:700; }
.account-email { font-size:11px; color:@avd_muted; }
.menu-head { font-weight:800; font-size:9px; letter-spacing:0.8px; color:@avd_faint; }
box.linked > button { min-height:26px; font-size:12px; font-weight:700;
  color:@avd_muted; background:@avd_bg; box-shadow:none; }
box.linked > button:hover { color:@avd_ink; }
box.linked > button:checked { background:@avd_accent; color:#ffffff; }
"""
