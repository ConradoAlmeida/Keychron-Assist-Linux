#!/usr/bin/env python3
"""Mostra qual tecla o teclado realmente envia (sem root, GTK4 + Wayland).

Uso: python3 tools/keypeek.py
Clique na janela e aperte a tecla. O nome do keyval é o que o GNOME recebe —
é exatamente isso que precisa ser F16 para o Quick Start funcionar.
"""

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

WINDOW_TEXT = "Aperte a tecla que você configurou"


class KeyPeek(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="keypeek")
        self.set_default_size(520, 220)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        self.hint = Gtk.Label(label=WINDOW_TEXT)
        self.hint.add_css_class("title-2")
        self.key = Gtk.Label(label="—")
        self.key.add_css_class("title-1")
        self.detail = Gtk.Label(label="")
        self.detail.add_css_class("dim-label")
        for widget in (self.hint, self.key, self.detail):
            box.append(widget)
        self.set_child(box)

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self.on_key)
        self.add_controller(controller)

    def on_key(self, _controller, keyval, keycode, state) -> bool:
        name = Gdk.keyval_name(keyval) or "?"
        mods = state & Gtk.accelerator_get_default_mod_mask()
        display = Gtk.accelerator_get_label(keyval, mods)
        entry = (f"<b>{GLib.markup_escape_text(display)}</b>\n"
                 f"keyval: {GLib.markup_escape_text(name)}  ·  keycode: {keycode}")
        self.key.set_markup(entry)
        self.detail.set_text("copie isso na conversa se não for F16")
        return False


class App(Gtk.Application):
    def do_activate(self) -> None:
        win = KeyPeek(self)
        win.present()


if __name__ == "__main__":
    raise SystemExit(App(application_id="dev.kassist.KeyPeek").run())
