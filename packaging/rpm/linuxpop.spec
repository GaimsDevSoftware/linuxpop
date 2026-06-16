%global appid io.github.GaimsDevSoftware.LinuxPop

Name:           linuxpop
Version:        0.9.2
Release:        1%{?dist}
Summary:        PopClip-style floating popup of context-aware actions for selected text

License:        MIT
URL:            https://github.com/GaimsDevSoftware/linuxpop
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3
Requires:       python3-gobject
Requires:       gtk3
Requires:       libayatana-appindicator-gtk3
Requires:       python3-xlib
Requires:       xdotool
Requires:       xclip
Requires:       xdg-utils
# Optional features — present on most desktops, pulled in when available:
Recommends:     wl-clipboard
Recommends:     wdotool
Recommends:     ydotool
Recommends:     qrencode
Recommends:     espeak-ng

%description
LinuxPop shows a floating popup of context-aware actions right above selected
text — search, open URLs/paths, send to an AI assistant, encode/decode,
translate, run shell commands — plus a clipboard manager and a no-code custom
button wizard. Runs on KDE Plasma 6 / Wayland (native popup positioning) and on
X11 desktops.

%prep
%autosetup -n %{name}-%{version}

%build
# Pure Python — nothing to compile.

%install
appdir=%{buildroot}%{_datadir}/%{name}
install -d "$appdir"
cp -a *.py platform_backend plugins_repo icons "$appdir"/

# Launcher on PATH
install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/%{name} <<EOF
#!/usr/bin/env bash
exec /usr/bin/python3 %{_datadir}/%{name}/main.py "\$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/%{name}

# Desktop entry, AppStream metadata, icon
install -Dm644 packaging/%{appid}.desktop      %{buildroot}%{_datadir}/applications/%{appid}.desktop
install -Dm644 packaging/%{appid}.metainfo.xml %{buildroot}%{_datadir}/metainfo/%{appid}.metainfo.xml
install -Dm644 icons/linuxpop.svg              %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_datadir}/%{name}
%{_datadir}/applications/%{appid}.desktop
%{_datadir}/metainfo/%{appid}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg

%changelog
* Mon Jun 15 2026 GaimsDev <raakanin@gmail.com> - 0.9.2-1
- Initial RPM packaging for Fedora / COPR.
