"""Menu-bar presence for flow.py — a mic icon with a status menu.

Gives the app a home when it runs headless (LaunchAgent, no terminal):
you can see it's alive, pick the dictation language, add or remove
languages, assign quick keys, and quit. Must be created on the main
thread, before AppHelper.runEventLoop().

Menu layout:
    local-flow — hold Right-Option to dictate
    model small.en · cleanup gemma3:4b
    ─────
    Language: Deutsch ▸   (enabled languages, checkmark on current)
    Quick keys ▸          (Q/W/E/R/T → language, while hotkey is held)
    Add language ▸        (full Whisper catalog, grouped A–Z)
    Remove language ▸
    ─────
    Quit local-flow
"""

import objc
from AppKit import (
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

import languages
import settings

GROUPS = ("A–D", "E–H", "I–M", "N–R", "S–Z")


def _group_for(name: str) -> str:
    c = name[:1].upper()
    for g in GROUPS:
        lo, hi = g.split("–")
        if lo <= c <= hi:
            return g
    return GROUPS[-1]


def _item(title, action=None, target=None, represented=None, enabled=True):
    entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title, action, "")
    if target is not None:
        entry.setTarget_(target)
    if represented is not None:
        entry.setRepresentedObject_(represented)
    entry.setEnabled_(enabled)
    return entry


class MenuBar(NSObject):

    def initWithStatusText_language_onLanguage_(self, status_text, language,
                                                 on_language):
        self = objc.super(MenuBar, self).init()
        if self is None:
            return None
        self.on_language = on_language
        self.language = language

        # keep a reference on self — a GC'd status item vanishes from the bar
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength)
        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "mic.fill", "local-flow")
        if icon is not None:
            icon.setTemplate_(True)   # adapts to light/dark menu bar
            self.item.button().setImage_(icon)
        else:
            self.item.button().setTitle_("🎙")

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.addItem_(_item("local-flow — hold Right-Option to dictate",
                            enabled=False))
        self.status = _item(status_text, enabled=False)
        menu.addItem_(self.status)
        menu.addItem_(NSMenuItem.separatorItem())

        self.language_item = _item("Language")
        self.language_item.setSubmenu_(NSMenu.alloc().initWithTitle_("Language"))
        menu.addItem_(self.language_item)

        self.quick_item = _item("Quick keys")
        self.quick_item.setSubmenu_(NSMenu.alloc().initWithTitle_("Quick keys"))
        menu.addItem_(self.quick_item)

        self.add_item = _item("Add language")
        self.add_item.setSubmenu_(self._build_catalog_menu())
        menu.addItem_(self.add_item)

        self.remove_item = _item("Remove language")
        self.remove_item.setSubmenu_(NSMenu.alloc().initWithTitle_("Remove"))
        menu.addItem_(self.remove_item)

        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit local-flow", b"terminate:", "q"))
        self.item.setMenu_(menu)
        self._rebuild()
        return self

    # -- building

    @objc.python_method
    def _build_catalog_menu(self):
        """Add language ▸ A–D ▸ Afrikaans (Afrikaans) …"""
        root = NSMenu.alloc().initWithTitle_("Add language")
        root.setAutoenablesItems_(False)
        groups = {}
        for code in languages.sorted_codes():
            g = _group_for(languages.english_name(code))
            if g not in groups:
                sub = NSMenu.alloc().initWithTitle_(g)
                sub.setAutoenablesItems_(False)
                holder = _item(g)
                holder.setSubmenu_(sub)
                root.addItem_(holder)
                groups[g] = sub
            groups[g].addItem_(_item(
                f"{languages.english_name(code)} — {languages.native_name(code)}",
                b"addLanguage:", self, code))
        return root

    @objc.python_method
    def _rebuild(self):
        """Refresh the Language, Quick keys and Remove submenus from settings."""
        enabled = settings.get_languages()
        keys = settings.get_quick_keys()
        key_for = {code: slot for slot, code in keys.items()}

        lang_menu = self.language_item.submenu()
        lang_menu.removeAllItems()
        lang_menu.setAutoenablesItems_(False)
        for code in enabled:
            title = languages.label(code)
            if code in key_for:
                title += f"    ⌥{key_for[code].upper()}"
            entry = _item(title, b"chooseLanguage:", self, code)
            entry.setState_(NSControlStateValueOn if code == self.language
                            else NSControlStateValueOff)
            lang_menu.addItem_(entry)
        self.language_item.setTitle_(
            f"Language: {languages.native_name(self.language)}")

        quick_menu = self.quick_item.submenu()
        quick_menu.removeAllItems()
        quick_menu.setAutoenablesItems_(False)
        quick_menu.addItem_(_item("While holding Right-Option, tap a key to "
                                  "switch language:", enabled=False))
        for slot in settings.QUICK_KEY_SLOTS:
            current = keys.get(slot)
            label = (f"{slot.upper()}  →  {languages.native_name(current)}"
                     if current else f"{slot.upper()}  →  (none)")
            holder = _item(label)
            sub = NSMenu.alloc().initWithTitle_(slot)
            sub.setAutoenablesItems_(False)
            for code in enabled:
                entry = _item(languages.label(code), b"assignQuickKey:", self,
                              {"slot": slot, "code": code})
                entry.setState_(NSControlStateValueOn if code == current
                                else NSControlStateValueOff)
                sub.addItem_(entry)
            sub.addItem_(NSMenuItem.separatorItem())
            sub.addItem_(_item("None", b"assignQuickKey:", self,
                               {"slot": slot, "code": None}))
            holder.setSubmenu_(sub)
            quick_menu.addItem_(holder)

        remove_menu = self.remove_item.submenu()
        remove_menu.removeAllItems()
        remove_menu.setAutoenablesItems_(False)
        for code in enabled:
            remove_menu.addItem_(_item(languages.label(code),
                                       b"removeLanguage:", self, code,
                                       enabled=len(enabled) > 1))

    # -- actions (main thread)

    def chooseLanguage_(self, sender):
        code = sender.representedObject()
        self._apply_language(code)
        if self.on_language:
            self.on_language(code)

    def addLanguage_(self, sender):
        code = sender.representedObject()
        settings.add_language(code)
        self._rebuild()

    def removeLanguage_(self, sender):
        code = sender.representedObject()
        settings.remove_language(code)
        if self.language == code:
            new = settings.get_language()
            self._apply_language(new)
            if self.on_language:
                self.on_language(new)
        self._rebuild()

    def assignQuickKey_(self, sender):
        info = sender.representedObject()
        settings.set_quick_key(info["slot"], info["code"])
        self._rebuild()

    # -- called from the app (any thread)

    def set_language(self, code):
        """Reflect a language change in the menu; safe from any thread."""
        AppHelper.callAfter(self._apply_language, code)

    @objc.python_method
    def _apply_language(self, code):
        self.language = code
        self._rebuild()

    def set_status(self, text):
        """Update the second line of the menu; safe from any thread."""
        AppHelper.callAfter(self.status.setTitle_, text)


def create_menubar(status_text, language="en", on_language=None):
    return MenuBar.alloc().initWithStatusText_language_onLanguage_(
        status_text, language, on_language)
