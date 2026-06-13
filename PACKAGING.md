# Packaging LinuxPop for Linux software stores

LinuxPop's primary distribution target is **Flathub**, because:

- It's the largest cross-distro Linux app store (40M+ installs/year).
- Every modern distro's software centre (GNOME Software, KDE Discover,
  Pop Shop, Mint Software Manager, elementary AppCenter) shows Flathub
  apps natively.
- Users don't need to add a PPA, run an install script, or trust a
  random tarball.

This document covers the **Flathub** path in detail and links to the
parallel **Snap Store** and distro-native (`.deb`, AUR) packaging.

---

## Status

What's done in this repo:

- [x] AppStream metainfo XML - `packaging/io.github.GaimsDevSoftware.LinuxPop.metainfo.xml`
- [x] Flatpak-style `.desktop` entry - `packaging/io.github.GaimsDevSoftware.LinuxPop.desktop`
- [x] Flatpak manifest with **real SHA256 hashes** - `packaging/flatpak/io.github.GaimsDevSoftware.LinuxPop.yml`
- [x] Wrapper script - `packaging/flatpak/linuxpop.wrapper`
- [x] Stable reverse-DNS app ID: `io.github.GaimsDevSoftware.LinuxPop`
- [x] `appstreamcli validate --no-net` passes (1 pedantic about online screenshots - expected)
- [x] `desktop-file-validate` passes
- [x] `install.sh` installs the AppStream metainfo + reverse-DNS `.desktop` so local
      tarball-installs are visible in Mint Software Manager / GNOME Software

What still needs human action before the first Flathub PR:

- [ ] Take 4 screenshots into `docs/screenshots/` (popup, settings, wizard,
      clipboard). Save as `popup.png`, `settings.png`, `wizard.png`,
      `clipboard.png` - those are the filenames the metainfo references.
      Use PrintScreen / scrot / gnome-screenshot, then commit + push.
- [ ] Install `flatpak-builder`: `sudo apt install flatpak-builder`
      (only `flatpak` is currently installed on this machine).
- [ ] Install the GNOME runtime + SDK:
      `flatpak install flathub org.gnome.Platform//46 org.gnome.Sdk//46`
- [ ] Build the Flatpak end-to-end:
      `flatpak-builder --user --install --force-clean build-dir packaging/flatpak/io.github.GaimsDevSoftware.LinuxPop.yml`
      and smoke-test: tray icon appears, hotkey works, popup appears on
      selection, clipboard hotkey opens picker.
- [ ] Submit PR to `flathub/flathub`.

---

## Flathub submission

### 1. One-time setup

```sh
sudo apt install flatpak flatpak-builder appstream-util
flatpak install flathub org.gnome.Platform//46 org.gnome.Sdk//46
```

### 2. Local build

```sh
flatpak-builder --user --install --force-clean build-dir \
    packaging/flatpak/io.github.GaimsDevSoftware.LinuxPop.yml
```

The first run will fail on the `PLACEHOLDER` hashes - `flatpak-builder`
prints the correct ones. Copy them into the manifest and run again.

### 3. Run the sandboxed build

```sh
flatpak run io.github.GaimsDevSoftware.LinuxPop
```

Smoke-test:

- Tray icon appears
- Selecting text in another app shows the LinuxPop popup
- The hotkey opens the clipboard picker
- Settings dialog opens
- The "New custom button" wizard works end-to-end

### 4. Submit to Flathub

1. Fork [flathub/flathub](https://github.com/flathub/flathub) on a new branch named after the app ID.
2. Add the manifest at the repo root.
3. Open a PR; Flathub's bot runs `flathub-linter` and the CI build.
4. Address review comments - most apps go through 1-3 review rounds.
5. Once merged, Flathub starts shipping it to every Linux software centre worldwide.

Flathub reviewer guidelines: <https://docs.flathub.org/docs/for-app-authors/requirements>.

---

## Snap Store (secondary)

Snap Store reaches Ubuntu users specifically (Snap is preinstalled on
recent Ubuntu releases). If you want a presence there too:

1. Create `snap/snapcraft.yaml` from the template at
   <https://snapcraft.io/docs/python-apps>.
2. The system tray + global hotkeys need `interfaces: [ x11, desktop, home, network, system-observe ]`.
3. Build locally with `snapcraft` in an LXD container.
4. Register the name (`snapcraft register linuxpop`) and push the snap.

The Snap container model is similar to Flatpak's but more restrictive
on X11 grabs - expect some integration work to get global hotkeys
working from inside a confined snap.

---

## Distro-native packages

### Arch User Repository (AUR)

LinuxPop is a Python script with a small set of runtime deps - the AUR
PKGBUILD is one of the cheapest packages you can write:

```sh
# /aur/linuxpop/PKGBUILD
pkgname=linuxpop
pkgver=0.1.0
pkgrel=1
pkgdesc="Floating popup of context-aware actions for selected text"
arch=('any')
url="https://github.com/GaimsDevSoftware/linuxpop"
license=('MIT')
depends=('python' 'gtk3' 'libhandy' 'libayatana-appindicator'
         'python-xlib' 'xdotool' 'xclip' 'wmctrl')
optdepends=('ollama: local AI plugin'
            'python-pillow: QR code plugin')
source=("$pkgname-$pkgver.tar.gz::https://github.com/GaimsDevSoftware/linuxpop/archive/v$pkgver.tar.gz")
sha256sums=('SKIP')

package() {
    cd "$srcdir/$pkgname-$pkgver"
    install -d "$pkgdir/usr/share/linuxpop"
    cp -r *.py icons plugins_repo recipes_repo "$pkgdir/usr/share/linuxpop/"
    install -Dm755 packaging/flatpak/linuxpop.wrapper "$pkgdir/usr/bin/linuxpop"
    install -Dm644 packaging/io.github.GaimsDevSoftware.LinuxPop.desktop \
        "$pkgdir/usr/share/applications/linuxpop.desktop"
    install -Dm644 packaging/io.github.GaimsDevSoftware.LinuxPop.metainfo.xml \
        "$pkgdir/usr/share/metainfo/linuxpop.metainfo.xml"
    install -Dm644 icons/linuxpop.svg \
        "$pkgdir/usr/share/icons/hicolor/scalable/apps/linuxpop.svg"
}
```

### Debian/Ubuntu (`.deb`)

Use `dh_python3` and a minimal `debian/` directory. The same files
above install to the same paths under `$pkgdir`. Build with
`debuild -us -uc`.

---

## What's intentionally not supported

- **Wayland.** LinuxPop relies on X11 global hotkey grabs, raw root
  pointer queries, and `xdotool`/`xclip`. The README documents this,
  and `main.py` refuses to start under pure Wayland. Adding Wayland
  support would require a portal-based redesign - out of scope for v0.x.

- **Auto-updating.** Each store handles updates itself. Don't ship an
  in-app updater.

- **Telemetry.** None. Don't add any.

---

## Reverse-DNS app ID

We use `io.github.GaimsDevSoftware.LinuxPop`. This:

- Matches the GitHub URL `github.com/GaimsDevSoftware/linuxpop`.
- Is accepted by Flathub (it follows their `io.github.<owner>.<app>` convention).
- Is identical between Flathub, the `.desktop` file, the AppStream metainfo,
  the Flatpak manifest, and the icon path - Linux software centres
  cross-reference these by ID, so they must match exactly.

If you ever own a domain like `linuxpop.app`, you can switch the ID to
`app.linuxpop.LinuxPop` - but that's a one-time migration with
implications for existing installs, so do it before the first Flathub
release if at all.
