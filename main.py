# -*- coding: utf-8 -*-
"""Module for z search ulauncher extension.

This module is an extension for ulauncher that searches for directories using
the 'z' file and sorts the results based on 'frecency'.
"""

import logging
import fileinput
import re
import time
import os
import sys
from pathlib import Path
from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import (
    KeywordQueryEvent,
    PreferencesEvent,
    PreferencesUpdateEvent,
    ItemEnterEvent,
)
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.item.ExtensionSmallResultItem import ExtensionSmallResultItem
from ulauncher.api.shared.action.ActionList import ActionList
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.OpenAction import OpenAction
from ulauncher.api.shared.action.DoNothingAction import DoNothingAction
from ulauncher.api.shared.action.RunScriptAction import RunScriptAction
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk


def frecency(rank, last_time):
    """Calculate 'frecency' from rank and last access time."""
    delta_t = time.time() - last_time
    multiplier = 0
    if delta_t < 3600:
        multiplier = 4
    elif delta_t < 86400:
        multiplier = 2
    elif delta_t < 604800:
        multiplier = 0.5
    else:
        multiplier = 0.25

    return rank * multiplier


def update_z_file(file, path, rank, time):
    """Update path in .z file with new rank and time."""
    logging.debug(
        "Updating '%s' in '%s' with rank %f and time %d", path, file, rank, int(time)
    )
    line_replaced = False
    for line in fileinput.input(file, inplace=1):
        old_path, old_rank, old_time = line.rsplit("|", maxsplit=3)
        if old_path == path:
            line = f"{path}|{rank}|{int(time)}" + os.linesep
            line_replaced = True
        sys.stdout.write(line)

    if not line_replaced:
        logging.warning(
            "Updating '%s' failed; '%s' not found in '%s'", file, path, file
        )


class ZSearchExtension(Extension):
    """The z search extension."""

    def __init__(self):
        """Initialize the base class and subscribe to events."""
        super(ZSearchExtension, self).__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())
        self.subscribe(PreferencesEvent, PreferencesLoadListener())
        self.subscribe(PreferencesUpdateEvent, PreferencesChangeListener())
        self.subscribe(ItemEnterEvent, ItemEnterEventListener())
        self.z_file = ""
        self.max_results = 0
        self.update_z_file = False
        self.command_on_select = ""

    def search(self, query):
        """Search for entries matching a query return a list of dicts.

        Apart from the rank and time values contained in the z file, the
        dicts contain calculated frecency as well.
        """
        results = []
        with open(self.z_file, "r") as z_file:
            lines = z_file.readlines()
            query = query.lower()
            pattern = re.compile(query, re.IGNORECASE)
            for line in lines:
                if pattern.search(line):
                    path, rank, last_time = line.rstrip("\n").split("|")
                    rank = float(rank)
                    last_time = float(last_time)
                    results.append(
                        {
                            "path": path,
                            "rank": rank,
                            "time": last_time,
                            "frecency": frecency(rank, last_time),
                        }
                    )

        sorted_results = sorted(results, key=lambda dic: dic["frecency"], reverse=True)
        return sorted_results[: self.max_results]


class PreferencesLoadListener(EventListener):
    """This event listener is called when the extension is loaded."""

    def on_event(self, event, extension):
        """Set extension member variables according to preferences."""
        extension.preferences.update(event.preferences)
        extension.z_file = os.path.expanduser(extension.preferences["z_file"])
        extension.max_results = int(extension.preferences["max_results"])
        extension.update_z_file = extension.preferences["update_z_file"] == "true"
        extension.command_on_select = extension.preferences["command_on_select"]


class PreferencesChangeListener(EventListener):
    """This event listener is called when the extension properties are changed."""

    def on_event(self, event, extension):
        """Update extension member variables when preferences change."""
        if event.id == "z_file":
            extension.z_file = os.path.expanduser(event.new_value)
        elif event.id == "max_results":
            extension.max_results = int(event.new_value)
        elif event.id == "update_z_file":
            extension.update_z_file = event.new_value == "true"
        elif event.id == "command_on_select":
            extension.command_on_select = event.new_value


class KeywordQueryEventListener(EventListener):
    """This event listener is called when the extension keyword is typed."""

    def __init__(self):
        """Initialize the base class and members."""
        super(KeywordQueryEventListener, self).__init__()
        self.folder_icon = self.get_folder_icon()

    def on_event(self, event, extension):
        """Run search if query was entered and act on results."""
        query = event.get_argument()
        if not query:
            return RenderResultListAction(
                [
                    ExtensionResultItem(
                        icon="images/icon.png",
                        name="Type a part of a directory name",
                        on_enter=DoNothingAction(),
                    )
                ]
            )

        results = extension.search(query)

        if not results:
            return RenderResultListAction(
                [
                    ExtensionResultItem(
                        icon="images/icon.png",
                        name="No results matching %s" % query,
                        on_enter=HideWindowAction(),
                    )
                ]
            )

        entries = []
        for result in results:
            actions = [RunScriptAction(f"{extension.command_on_select} {result['path']}")]

            if extension.update_z_file:
                actions.append(ExtensionCustomAction(result, keep_app_open=False))
            entries.append(
                ExtensionSmallResultItem(
                    icon=self.folder_icon,
                    name=self.get_display_path(result["path"]),
                    on_enter=ActionList(actions),
                )
            )

        return RenderResultListAction(entries)

    def get_display_path(self, path):
        """Strip /home/user from path if appropriate."""
        path = Path(path)
        home = Path.home()
        if home in path.parents:
            return "~/" + str(path.relative_to(home))
        else:
            return str(path)

    def get_folder_icon(self):
        """Get a path to a reasonable folder icon.

        Fall back to an included one if none is found.
        """
        file = Gio.File.new_for_path("/")
        folder_info = file.query_info("standard::icon", 0, Gio.Cancellable())
        folder_icon = folder_info.get_icon().get_names()[0]
        icon_theme = Gtk.IconTheme.get_default()
        icon_folder = icon_theme.lookup_icon(folder_icon, 128, 0)
        if icon_folder:
            folder_icon = icon_folder.get_filename()
        else:
            folder_icon = "images/folder.png"

        return folder_icon


class ItemEnterEventListener(EventListener):
    """This event listener is called when the user selects an entry."""

    def on_event(self, event, extension):
        """Update the entry's rank and time in the z file."""
        entry = event.get_data()
        update_z_file(
            extension.z_file, entry["path"], entry["rank"] + 1, float(time.time())
        )


if __name__ == "__main__":
    ZSearchExtension().run()
