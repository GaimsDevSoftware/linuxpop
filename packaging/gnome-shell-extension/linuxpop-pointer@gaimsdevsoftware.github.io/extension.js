// LinuxPop Shell helper - exposes two things an app cannot get for itself on
// GNOME Wayland, over D-Bus:
//
//   GetPointer()    -> the global cursor position (logical coords). XQueryPointer
//                      freezes over native-Wayland surfaces and GNOME never
//                      shipped the wlr virtual-pointer / layer-shell protocols,
//                      so the Shell's own global.get_pointer() is the only
//                      reliable source. LinuxPop anchors its popup at the cursor.
//
//   ActivateApp()   -> re-focus the most-recently-used normal window. Clicking
//                      the popup hands keyboard focus to it, so keystroke
//                      actions (Cut / Paste / Select-all / Backspace) would
//                      inject into the popup instead of the user's app. The
//                      popup is a POPUP_MENU (not a NORMAL window), so the
//                      MRU NORMAL window is the app we must restore focus to
//                      before injecting.
//
// The object is exported on gnome-shell's own bus connection, so it is reached
// at the well-known name org.gnome.Shell.

import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="io.github.GaimsDevSoftware.LinuxPop.Pointer">
    <method name="GetPointer">
      <arg type="i" direction="out" name="x"/>
      <arg type="i" direction="out" name="y"/>
    </method>
    <method name="ActivateApp"/>
  </interface>
</node>`;

const OBJECT_PATH = '/io/github/GaimsDevSoftware/LinuxPop/Pointer';

export default class LinuxPopPointerExtension extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
    }

    disable() {
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
    }

    // Logical (stage) coordinates, the same space Gtk.Window.move() and the
    // monitor geometry use, so the popup needs no scale conversion.
    GetPointer() {
        const [x, y] = global.get_pointer();
        return [x, y];
    }

    // Re-focus the app the popup stole focus from. The MRU NORMAL window is
    // that app (the popup is a POPUP_MENU, not in the NORMAL list).
    ActivateApp() {
        const wins = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
        if (wins && wins.length > 0)
            wins[0].activate(global.get_current_time());
    }
}
