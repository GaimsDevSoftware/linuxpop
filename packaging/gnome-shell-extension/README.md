# GNOME Shell helper extension

`linuxpop-pointer@gaimsdevsoftware.github.io` is a tiny GNOME Shell extension
that exposes, over D-Bus (under `org.gnome.Shell`), two things a normal app
cannot obtain for itself on GNOME Wayland:

- **`GetPointer()`** — the global cursor position. `XQueryPointer` freezes over
  native-Wayland windows and GNOME never shipped the wlr virtual-pointer /
  layer-shell protocols, so the Shell's own `global.get_pointer()` is the only
  reliable source. LinuxPop uses it to anchor the selection popup at the cursor.
- **`ActivateApp()`** — re-focus the most-recently-used normal window. Clicking
  the popup hands keyboard focus to it, so a keystroke action (Cut / Paste /
  Select-all / Backspace) would inject into the popup; this restores focus to
  the user's app first.

KDE exposes the equivalent through KWin scripts; on X11 the X server answers
directly. Only GNOME Wayland needs this shim.

## Install

It is installed and enabled automatically on first run on GNOME Wayland (the app
copies it to `~/.local/share/gnome-shell/extensions/` and adds it to
`org.gnome.shell enabled-extensions`). GNOME loads extension code only at login,
so it activates after the next log out / log in. Manual install:

```bash
cp -r "linuxpop-pointer@gaimsdevsoftware.github.io" \
   ~/.local/share/gnome-shell/extensions/
gnome-extensions enable linuxpop-pointer@gaimsdevsoftware.github.io   # after re-login
```
