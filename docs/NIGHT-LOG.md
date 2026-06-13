# Natt-logg — Fedora KDE-port (autonomt arbeid)

Robert sover; jeg jobber autonomt gjennom fasene. Avgjørelser jeg tar på egen
hånd er merket **[BESLUTNING]** med begrunnelse. Reelle spørsmål til Robert
samles nederst under **Spørsmål til morgenen**.

Miljø: dev = Ubuntu 24.04 / Cinnamon / X11. Target = Fedora 44 KDE Plasma,
KWin 6.6.5, Python 3.14.5, nås via `ssh fedora` (Tailscale 100.96.59.67).
Passordløs `dnf` er satt opp på Fedora.

---

## Fase 0 — Spike (resultater)

Throwaway-prototype i `spike/`, kjørt på ekte Fedora KDE Wayland.

| Sjekk | Resultat | Detalj |
|---|---|---|
| `--check cursor` | **GO** | `workspace.cursorPos` via KWin-script + `callDBus`: round-trip **3.9 ms** (terskel for GO var 50 ms) |
| `--check layer` | **GO** | gtk-layer-shell anker topp-venstre + margin = (x,y) plasserte popupen fritt midt på skjermen, bekreftet visuelt i Roberts skjermbilde — IKKE snappet til kant |
| `--check full` | (se under) | drives selvdrevet med `wl-copy --primary` |

**Konklusjon så langt: GO.** Begge byggeklossene for «popup ved markøren» på
KWin Wayland fungerer, og raskt nok til å føles umiddelbart. Den største
risikoen i hele planen er avkreftet.

---

## STATUS NÅR DU VÅKNER (kort versjon)

**LinuxPop kjører på Fedora KDE Plasma Wayland, og kjernefunksjonen virker.**
Marker tekst → action-popup dukker opp ved markøren med alle knappene. Bekreftet
med skjermbilde (popupen rendret via gtk-layer-shell, korrekt posisjonert).

Arbeidet ligger **ucommittet** i worktreet (du ba ikke om commit — review diffen
først). Ny pakke: `platform_backend/`. Endret: `main.py`, `actions.py`,
`popup.py`. Appen er rsyncet til Fedora under `~/linuxpop-wl/`.

### Verifisert at virker på Fedora KDE Wayland
- App starter (session-gate slipper KDE Wayland gjennom) ✓
- Selection-watcher (`wl-paste --primary --watch`) trigger på markering ✓
- **Popup rendrer + posisjoneres ved markøren** via gtk-layer-shell ✓ (skjermbilde)
- Clipboard copy/read (wl-clipboard) ✓
- Markørposisjon via KWin `cursorPos` (subprocess-hjelper) ✓
- Tray-ikon (legacy AppIndicator3 → KDE SNI) ✓
- X11 uendret: appen starter og kjører som før på denne Cinnamon-maskinen ✓
- Deps installert på Fedora: `gtk-layer-shell`, `wtype`, `libhandy`

### Gaps som trenger DEG (kan ikke verifiseres uten deg)
1. **Global hotkey-trykk.** KGlobalAccel *registrerer* seg nå (super+shift+y og
   super+v vises i loggen som «registered … press to verify»). Men selve
   tasten-trykk-leverer-signalet kan jeg ikke teste uten at du trykker. **Test:
   start appen på Fedora, trykk Super+Shift+Y — dukker popupen opp?** Qt-keycode/
   flags-encodingen er et research-basert beste-gjett ([BESLUTNING] i wayland_kde.py).
2. **Esc-for-å-lukke** popupen er av på Wayland (layer-shell KeyboardMode.NONE,
   så popupen ikke stjeler fokus fra tekst-appen). Auto-skjul via timere virker.
3. **Klikk-utenfor-for-å-lukke** er mindre presis enn X11 (avhenger av timere +
   GDK-pointer; ikke verifisert at den føles bra).

### Spørsmål til morgenen (svar når du vil)
1. Hotkey-strategi: hvis KGlobalAccel-trykket *ikke* funker, foretrekker du
   (a) at jeg graver videre i KGlobalAccel-encodingen, eller (b) en enklere KDE-vei
   der du binder en custom-snarvei i Systeminnstillinger til en kommando som poker
   daemonen? Min anbefaling: prøv (a) først siden registreringen alt lykkes.
2. Esc/klikk-utenfor-dismiss: er auto-skjul-på-timer godt nok for v1, eller vil du
   at jeg prioriterer ekte Esc + klikk-utenfor på Wayland (krever litt mer arbeid
   med fokus-håndtering)? Anbefaling: timer-dismiss er godt nok for v1.
3. Skal jeg committe arbeidet på branchen når du har sett diffen, eller vil du
   gjøre det selv?

## Gjenstående (mindre, dokumentert)
- Dobbeltklikk-popup (XRecord) har ingen Wayland-ekvivalent — av.
- OCR-hotkey trenger region-verktøy (maim/spectacle-region) — ikke testet.
- Markør-latens: ~100-150 ms per markering (python-subprocess per cursorPos).
  Akseptabelt for v1; «resident KWin-script» er i backlog som optimalisering.

## Fikset også i natt (etter første status)
- **Blocklist på Wayland virker nå** via nytt KWin-script `_kwin_active.py`
  (`active_window_haystacks()` rapporterer resourceClass+caption; testet: returnerte
  `['claude','claude']`). Kjøres kun når brukeren faktisk har blocklist-mønstre.
- About/argparse-tekst er nå backend-bevisst i stedet for hardkodet «(X11)».

## Slik kjører du det på Fedora selv
```sh
ssh-en er alt satt opp; på Fedora:
cd ~/linuxpop-wl
XDG_SESSION_TYPE=wayland python3 main.py --debug
# eller bare: python3 main.py   (auto-detekterer wayland_kde-backend)
```
Logg: `~/.cache/linuxpop/linuxpop.log`. Backend velges av `XDG_SESSION_TYPE`;
overstyr med `LINUXPOP_BACKEND=x11|wayland_kde`.

## Fedora KDE runtime-deps (for README/pakking)
`python3-gobject gtk3 gtk-layer-shell wl-clipboard wtype libhandy
libappindicator-gtk3 python3-dbus` + valgfritt `tesseract maim` (OCR).

## Logg (kronologisk)
- Fase 0 spike: cursor 3.9ms, layer-shell + selection-watch + clipboard alle GO.
- Fase 1: `platform_backend/` (base/x11/wayland_kde/__init__/_kwin_cursor),
  refaktorert main/actions/popup bak backenden. X11 verifisert uendret.
- Fase 2: Wayland-backend bygd. Fant + fikset: (a) popup.py importerte Xlib på
  toppnivå → gjort valgfri (Fedora har ikke python-xlib); (b) KGlobalAccel manglet
  DBusGMainLoop → fikset, registrering lykkes nå; (c) layer-shell KeyboardMode
  ON_DEMAND ga fokus-stjeling → satt til NONE. Popup-render bekreftet via probe +
  to skjermbilder (probe + ekte app ende-til-ende). Tray + libhandy verifisert/installert.
- Blocklist på Wayland: nytt `_kwin_active.py`-script, testet OK.
- Fase 4 (delvis): Flatpak-manifestet har nå `--socket=wayland` +
  `--talk-name=org.kde.KWin` + `--talk-name=org.kde.kglobalaccel`. Gjenstår å bunte
  wl-clipboard/wtype/gtk-layer-shell som moduler (ekte URL+sha256) + et bygg —
  ikke gjort blindt. Dokumentert som TODO i manifestet.

## Stoppet her — venter på dine svar
Jeg nådde punktet du ba om: kjernen virker, og videre arbeid (hotkey-trykk-
verifisering, dismiss-polish, Flatpak-bygg) krever enten din input (spørsmålene
over) eller et bygg jeg ikke kan verifisere autonomt. God morgen!
