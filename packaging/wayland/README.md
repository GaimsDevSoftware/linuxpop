# Wayland keystroke injection setup (native installs)

On Wayland (GNOME, KDE) the synthetic-input ban means LinuxPop can only inject
Cut / Paste / Backspace / Select-all through **ydotool**, which writes to
`/dev/uinput`. The **Flatpak bundles and starts ydotoold itself**, so this is
only needed for **native** installs (RPM / deb / running from source).

It's a one-time setup. The packaged `ydotool.service` (a *system* service) runs
ydotoold as **root** on a root-only socket the app can't reach — so we run a
**per-user** ydotoold instead.

```bash
# 1. Install ydotool and join the 'input' group
sudo dnf install ydotool          # Fedora   (apt install ydotool on Debian/Mint)
sudo usermod -aG input "$USER"

# 2. Let the 'input' group use /dev/uinput
sudo install -m0644 99-uinput.rules /etc/udev/rules.d/99-uinput-linuxpop.rules
echo uinput | sudo tee /etc/modules-load.d/uinput.conf
sudo modprobe uinput
sudo udevadm control --reload-rules && sudo udevadm trigger /dev/uinput

# 3. Run ydotoold as your user (retire the root system one if the distro enabled it)
sudo systemctl disable --now ydotool.service 2>/dev/null || true
install -Dm0644 ydotoold.service ~/.config/systemd/user/ydotoold.service
systemctl --user daemon-reload
systemctl --user enable --now ydotoold.service

# 4. Log out and back in (so the 'input' group takes effect), then run LinuxPop.
```

LinuxPop points `YDOTOOL_SOCKET` at `$XDG_RUNTIME_DIR/.ydotool_socket`, which the
service creates. The same setup works on GNOME (`xwayland_gnome` backend) and KDE
(`wayland_kde` backend). X11 sessions don't need any of this — they use `xdotool`.
