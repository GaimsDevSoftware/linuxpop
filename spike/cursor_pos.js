// KWin script for the Fase 0 spike.
//
// Wayland gives no protocol way for a client to query the global pointer
// position. KDE's KWin scripting API DOES expose it as `workspace.cursorPos`.
// Instead of printing to journalctl (slow, and it bloats the journal), we use
// callDBus to push the coordinates straight back to the spike process — this
// is the technique a production Wayland/KDE backend would use, and it lets the
// spike measure the real round-trip latency.
//
// The spike loads + runs this via:
//   org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript
//   org.kde.KWin /Scripting/Script<N> org.kde.kwin.Script.run
callDBus(
    "org.linuxpop.SpikeCursor",   // bus name the spike registers
    "/cursor",                    // object path
    "org.linuxpop.SpikeCursor",   // interface
    "Report",                     // method
    workspace.cursorPos.x,
    workspace.cursorPos.y
);
