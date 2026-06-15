# Fedora KDE Plasma - utsatt backlog

Ting vi bevisst har skjøvet ut av hovedløpet (Fase 0–5) for KDE/Wayland-porten.
Ikke blokkere, men verdt å plukke fra når vi har tid. Kryss av / flytt opp i en
fase når noe blir aktuelt.

Hovedplanen og fasene ligger i tasklisten; dette er det som *ikke* er i den.

## Utsatt til v2 (gjør først når v1 Wayland-port er stabil)

- [ ] **Qt/QML-port av popup-UI.** Behold GTK3 i v1. Vurder Qt kun hvis brukere
      faktisk klager på GTK-looken på Plasma. Isolert bytte bak `platform/`-laget.
- [ ] **Native Qt-tray** i stedet for libayatana-appindicator (SNI). Bare hvis
      SNI-trayen viser seg å oppføre seg dårlig på Plasma 6 (verifiseres i Fase 3).
- [ ] **Wayland på andre compositors enn KDE.** `workspace.cursorPos` er
      KDE-spesifikk. GNOME Wayland / wlroots trenger en annen markør-strategi
      (eller hotkey-only). Først relevant hvis vi vil utvide utover Fedora KDE.

## Ytelses-/robusthetsoppgaver (gjør hvis spiken viser behov)

- [ ] **Resident KWin-script** i stedet for load-run-per-event, hvis Fase 0-spiken
      måler for høy markør-latens (>~150 ms). Holder ett script i live som pusher
      posisjon på forespørsel.
- [ ] **`ydotool` som alternativ til `wtype`** for auto-paste, hvis `wtype` ikke
      dekker alle apper (ydotool krever uinput-daemon - mer oppsett).
- [ ] **HiDPI / fraksjonell skalering** grundig testet på Plasma (X11-versjonen er
      mest testet på 2× skalering - se README «Limitations»).

## Pakking (sekundært til Flatpak/Flathub)

- [ ] **COPR-repo + native RPM** for `dnf install` uten Flatpak. Fase 4 prioriterer
      Flatpak (dekker Discover automatisk); COPR er nice-to-have.
- [ ] **Snap Store.** Allerede nevnt som sekundært i [PACKAGING.md](../PACKAGING.md);
      mer restriktiv på input-grabs. Lav prioritet for KDE-publikum.

## Oppdaget under Fase 2-implementasjon (Wayland-spesifikt)

- [ ] **Esc + klikk-utenfor-dismiss på Wayland.** Popupen bruker layer-shell
      KeyboardMode.NONE (tar ikke fokus, så paste går til kilde-appen). Det gjør
      at Esc-via-GTK ikke virker og klikk-utenfor er mindre presis. Auto-skjul på
      timer virker. Fiks krever smartere fokus-håndtering (evt. ON_DEMAND + ignorere
      den første focus-out, eller en separat dismiss-overlay).
- [ ] **Aktivt-vindu-blocklist på Wayland.** `active_window_haystacks()` returnerer
      [] i dag → blocklist er av. Implementer via KWin-script som rapporterer
      `workspace.activeWindow.resourceClass` + caption over DBus (samme mønster som
      cursor-hjelperen).
- [ ] **Global hotkey-verifisering (KGlobalAccel).** Registrering lykkes; selve
      tasten-trykk-leverer-signalet må verifiseres av Robert. Hvis Qt-keycode/flags-
      encodingen er feil, juster (eller bytt til custom-snarvei-i-Systeminnstillinger-
      mot-daemon-IPC). Se NIGHT-LOG spørsmål 1.
- [ ] **About-dialog sier «(X11)».** Kosmetisk; gjør backend-bevisst i main.py
      open_about / argparse-description.
- [ ] **Tray-ikon på Fedora.** Kjører (legacy AppIndicator3 → SNI), men ikonet
      kan vise fallback fordi install.sh ikke kjøres ved rsync-deploy; sørg for at
      ikonet ligger i ikon-søkestien ved ekte installasjon/pakking.

## Bevisst IKKE støttet (ikke backlog - avklart bortvalg)

- Auto-oppdatering i appen (hver store håndterer det selv).
- Telemetri (ingen, noensinne).
