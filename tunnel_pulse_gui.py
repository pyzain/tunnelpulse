"""
TunnelPulse — Dashboard & Compiled Entry Point
================================================
Prepared by Zain Raza

This is the ONLY file that touches widgets. All state, networking, and
tunnel control lives in tunnel_pulse_core.py — this file reads from it
and calls into it, never duplicates its logic (per project policy).

Threading model:
  * The engine runs its own background thread (started inside
    TunnelPulseEngine). This GUI never blocks the Tk main loop: every
    engine call that can take real time (connect/disconnect/import/
    verify/optimize) runs on a short-lived worker thread, and results
    come back through the engine's thread-safe EventBus, which this
    window polls on a normal `.after()` timer. No widget is ever
    touched from a non-main thread.

Theme model:
  * Every color is defined ONCE as a (light, dark) tuple and handed to
    CustomTkinter widgets directly — CTk resolves the right half of the
    tuple automatically when the appearance mode changes. This is what
    avoids the classic "toggled dark mode and three buttons stayed the
    wrong color" bug: there is no second code path that re-colors
    things by hand. The one exception is the plain Tk Canvas used for
    the speed graph, which CTk doesn't theme automatically — that one
    explicitly redraws itself on toggle (see Sparkline.apply_theme).
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Optional

import customtkinter as ctk

import tunnel_pulse_core as core

# ---- THEME  (single source of truth — see module docstring) ----

class Theme:
    BG = ("#F4F5F8", "#0F1216")
    SURFACE = ("#FFFFFF", "#171B21")
    SURFACE_ALT = ("#ECEEF3", "#1D222A")
    BORDER = ("#E1E4EA", "#262C35")
    TEXT_PRIMARY = ("#15171C", "#E7E9ED")
    TEXT_SECONDARY = ("#6B7280", "#8B93A3")
    ACCENT = ("#3D63FF", "#5C7FFF")
    ACCENT_TEXT = ("#FFFFFF", "#0F1216")
    SUCCESS = ("#1E9E5A", "#3FD983")
    WARNING = ("#B4790F", "#F2B84B")
    DANGER = ("#D3453F", "#F2695F")
    NEUTRAL = ("#9AA1AC", "#5B6270")

    @staticmethod
    def resolve(pair: tuple[str, str]) -> str:
        return pair[0] if ctk.get_appearance_mode() == "Light" else pair[1]


STATE_STYLE: dict[core.ConnectionState, tuple[tuple, str]] = {
    core.ConnectionState.DISCONNECTED: (Theme.NEUTRAL, "Disconnected"),
    core.ConnectionState.VALIDATING: (Theme.ACCENT, "Checking\u2026"),
    core.ConnectionState.CONNECTING: (Theme.ACCENT, "Connecting\u2026"),
    core.ConnectionState.CONNECTED: (Theme.SUCCESS, "Connected"),
    core.ConnectionState.RECONNECTING: (Theme.WARNING, "Reconnecting\u2026"),
    core.ConnectionState.DISCONNECTING: (Theme.NEUTRAL, "Disconnecting\u2026"),
    core.ConnectionState.DEGRADED: (Theme.WARNING, "Degraded"),
    core.ConnectionState.FAILED: (Theme.DANGER, "Failed"),
}


# ---- SMALL REUSABLE WIDGETS ----

class StatusPill(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=Theme.SURFACE_ALT, corner_radius=999)
        self._dot = ctk.CTkLabel(self, text="\u25CF", text_color=Theme.NEUTRAL,
                                  font=ctk.CTkFont(size=14), width=14)
        self._dot.pack(side="left", padx=(12, 2), pady=6)
        self._label = ctk.CTkLabel(self, text="Disconnected", text_color=Theme.TEXT_PRIMARY,
                                    font=ctk.CTkFont(size=13, weight="bold"))
        self._label.pack(side="left", padx=(0, 14), pady=6)

    def set_state(self, state: core.ConnectionState) -> None:
        color, text = STATE_STYLE.get(state, (Theme.NEUTRAL, state.name.title()))
        self._dot.configure(text_color=color)
        self._label.configure(text=text)


class StatCard(ctk.CTkFrame):
    def __init__(self, master, title: str):
        super().__init__(master, fg_color=Theme.SURFACE, corner_radius=14,
                          border_width=1, border_color=Theme.BORDER)
        self._title = ctk.CTkLabel(self, text=title.upper(), text_color=Theme.TEXT_SECONDARY,
                                    font=ctk.CTkFont(size=11, weight="bold"))
        self._title.pack(anchor="w", padx=16, pady=(14, 0))
        self._value = ctk.CTkLabel(self, text="\u2014", text_color=Theme.TEXT_PRIMARY,
                                    font=ctk.CTkFont(size=22, weight="bold"))
        self._value.pack(anchor="w", padx=16, pady=(2, 0))
        self._subtitle = ctk.CTkLabel(self, text="", text_color=Theme.TEXT_SECONDARY,
                                       font=ctk.CTkFont(size=12))
        self._subtitle.pack(anchor="w", padx=16, pady=(0, 14))

    def update_value(self, value: str, subtitle: str = "", tone: tuple = None) -> None:
        self._value.configure(text=value, text_color=tone or Theme.TEXT_PRIMARY)
        self._subtitle.configure(text=subtitle)


class Sparkline(ctk.CTkCanvas):
    """Plain Tk canvas — CustomTkinter doesn't theme these automatically,
    so it explicitly redraws itself whenever the app toggles theme."""

    def __init__(self, master, height: int = 90):
        super().__init__(master, height=height, highlightthickness=0,
                          bg=Theme.resolve(Theme.SURFACE))
        self._down: list[float] = []
        self._up: list[float] = []

    def push(self, down_mbps: float, up_mbps: float) -> None:
        self._down = (self._down + [down_mbps])[-60:]
        self._up = (self._up + [up_mbps])[-60:]
        self.redraw()

    def apply_theme(self) -> None:
        self.configure(bg=Theme.resolve(Theme.SURFACE))
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), 1)
        peak = max([1.0, *self._down, *self._up])

        def line(values, color):
            if len(values) < 2:
                return
            step = w / max(len(self._down) - 1, 1)
            points = []
            for idx, v in enumerate(values):
                x = idx * step
                y = h - (v / peak) * (h - 10) - 4
                points.extend([x, y])
            self.create_line(*points, fill=color, width=2, smooth=True)

        line(self._down, Theme.resolve(Theme.ACCENT))
        line(self._up, Theme.resolve(Theme.NEUTRAL))


class IssueLog(ctk.CTkTextbox):
    MAX_LINES = 200  # bounded — never let this become an unbounded memory leak

    def __init__(self, master):
        super().__init__(master, height=110, fg_color=Theme.SURFACE_ALT,
                          text_color=Theme.TEXT_SECONDARY, font=ctk.CTkFont(size=12),
                          wrap="word")
        self.configure(state="disabled")

    def add(self, message: str, severity: str = "info") -> None:
        prefix = {"critical": "\u26A0", "warning": "\u26A0", "info": "\u2022"}.get(severity, "\u2022")
        line = f"{time.strftime('%H:%M:%S')}  {prefix}  {message}\n"
        self.configure(state="normal")
        self.insert("end", line)
        # trim from the top if we've grown past MAX_LINES
        if float(self.index("end-1c").split(".")[0]) > self.MAX_LINES:
            self.delete("1.0", "2.0")
        self.see("end")
        self.configure(state="disabled")


# ---- MAIN APPLICATION WINDOW ----

class TunnelPulseApp(ctk.CTk):
    POLL_INTERVAL_MS = 250
    PROTECTION_RECHECK_SECS = 60

    def __init__(self, debug: bool = False):
        super().__init__()
        self.debug = debug
        self.title("TunnelPulse")
        self.geometry("620x640")
        self.minsize(560, 600)
        self.configure(fg_color=Theme.BG)

        self.engine = core.TunnelPulseEngine()
        self.engine.start_background_loop()

        self._connect_start: Optional[float] = None
        self._last_protection_check = 0.0
        self._busy = False  # guards against double-clicking Connect mid-action
        self._callback_queue: "queue.Queue[tuple[Callable, object]]" = queue.Queue()

        self._build_ui()
        self._refresh_profile_list()
        self._run_startup_check()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(self.POLL_INTERVAL_MS, self._poll_events)
        self.after(1000, self._tick_uptime)

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(18, 8))

        ctk.CTkLabel(header, text="TunnelPulse", text_color=Theme.TEXT_PRIMARY,
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")

        self.theme_switch = ctk.CTkSwitch(header, text="Dark mode", command=self._toggle_theme,
                                           progress_color=Theme.ACCENT)
        self.theme_switch.pack(side="right")
        if ctk.get_appearance_mode() == "Dark":
            self.theme_switch.select()

        self.status_pill = StatusPill(header)
        self.status_pill.pack(side="right", padx=12)

        # -- control row: profile + import + connect --
        controls = ctk.CTkFrame(self, fg_color=Theme.SURFACE, corner_radius=14,
                                 border_width=1, border_color=Theme.BORDER)
        controls.pack(fill="x", padx=20, pady=8)
        self._controls_frame = controls  # explicit anchor for the setup banner below

        self.profile_menu = ctk.CTkOptionMenu(controls, values=["No profiles yet"],
                                               fg_color=Theme.SURFACE_ALT,
                                               button_color=Theme.ACCENT,
                                               text_color=Theme.TEXT_PRIMARY)
        self.profile_menu.pack(side="left", padx=14, pady=14)

        ctk.CTkButton(controls, text="Import .conf", width=110, fg_color=Theme.SURFACE_ALT,
                      text_color=Theme.TEXT_PRIMARY, hover_color=Theme.BORDER,
                      command=self._on_import_clicked).pack(side="left", padx=6, pady=14)

        self.connect_btn = ctk.CTkButton(controls, text="Connect", width=120,
                                          fg_color=Theme.ACCENT, text_color=Theme.ACCENT_TEXT,
                                          command=self._on_connect_clicked)
        self.connect_btn.pack(side="right", padx=14, pady=14)

        # -- setup banner (hidden unless something needs attention) --
        self.setup_banner = ctk.CTkLabel(self, text="", text_color=Theme.WARNING,
                                          fg_color=Theme.SURFACE_ALT, corner_radius=10,
                                          font=ctk.CTkFont(size=12), wraplength=560, justify="left")
        # packed on demand by _show_banner / hidden by _hide_banner

        # -- stat grid --
        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=8)
        for c in range(3):
            grid.grid_columnconfigure(c, weight=1, uniform="col")

        self.card_download = StatCard(grid, "Download")
        self.card_upload = StatCard(grid, "Upload")
        self.card_uptime = StatCard(grid, "Uptime")
        self.card_data = StatCard(grid, "Session data")
        self.card_protection = StatCard(grid, "Protection")
        self.card_link = StatCard(grid, "Link health")

        cards = [self.card_download, self.card_upload, self.card_uptime,
                 self.card_data, self.card_protection, self.card_link]
        for idx, card in enumerate(cards):
            card.grid(row=idx // 3, column=idx % 3, sticky="nsew", padx=6, pady=6)

        # -- sparkline --
        graph_frame = ctk.CTkFrame(self, fg_color=Theme.SURFACE, corner_radius=14,
                                    border_width=1, border_color=Theme.BORDER)
        graph_frame.pack(fill="x", padx=20, pady=8)
        ctk.CTkLabel(graph_frame, text="LIVE THROUGHPUT", text_color=Theme.TEXT_SECONDARY,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=16, pady=(12, 0))
        self.sparkline = Sparkline(graph_frame)
        self.sparkline.pack(fill="x", padx=16, pady=(4, 14))

        # -- actions row: verify / optimize --
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(0, 4))
        ctk.CTkButton(actions, text="Verify protection now", fg_color=Theme.SURFACE_ALT,
                      text_color=Theme.TEXT_PRIMARY, hover_color=Theme.BORDER,
                      command=self._on_verify_clicked).pack(side="left")
        ctk.CTkButton(actions, text="Optimize link", fg_color=Theme.SURFACE_ALT,
                      text_color=Theme.TEXT_PRIMARY, hover_color=Theme.BORDER,
                      command=self._on_optimize_clicked).pack(side="left", padx=8)

        # -- issue log --
        ctk.CTkLabel(self, text="ACTIVITY", text_color=Theme.TEXT_SECONDARY,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=24, pady=(10, 0))
        self.issue_log = IssueLog(self)
        self.issue_log.pack(fill="both", expand=True, padx=20, pady=(4, 8))

        # -- footer --
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(0, 14))
        self.admin_badge = ctk.CTkLabel(footer, text="", text_color=Theme.TEXT_SECONDARY,
                                         font=ctk.CTkFont(size=11))
        self.admin_badge.pack(side="left")
        ctk.CTkLabel(footer, text="Prepared by Zain Raza  \u00B7  v1.0.0",
                     text_color=Theme.TEXT_SECONDARY, font=ctk.CTkFont(size=11)).pack(side="right")

        self.admin_badge.configure(
            text="Administrator" if core.PrivilegeManager.is_admin() else "Standard user — will prompt on connect"
        )

    # ---- startup self-check (graceful degrade, not a crash) --------------

    def _run_startup_check(self) -> None:
        issues = self.engine.locator
        if issues.wireguard_exe is None:
            self._show_banner(
                "WireGuard component not found. Install WireGuard for Windows once, "
                "or place wireguard.exe + wintun.dll in this app's bin/ folder, then restart.",
                Theme.WARNING,
            )
        elif not self.engine.config_store.list_profiles():
            self._show_banner(
                "No VPN profile yet. Click \u2018Import .conf\u2019 to add one from your server.",
                Theme.ACCENT,
            )

    def _show_banner(self, text: str, color: tuple) -> None:
        self.setup_banner.configure(text=text, text_color=color)
        self.setup_banner.pack(fill="x", padx=20, pady=(0, 8), after=self._controls_frame)

    def _hide_banner(self) -> None:
        self.setup_banner.pack_forget()

    # ---- theme ------------------------------------------------------------

    def _toggle_theme(self) -> None:
        mode = "Dark" if self.theme_switch.get() else "Light"
        ctk.set_appearance_mode(mode)
        self.sparkline.apply_theme()
        self.status_pill.set_state(self.engine.state)  # re-resolve dot color for new mode

    # ---- profile handling ---------------------------------------------------

    def _refresh_profile_list(self) -> None:
        profiles = self.engine.config_store.list_profiles()
        self._profiles_by_name = {p.name: p for p in profiles}
        names = list(self._profiles_by_name) or ["No profiles yet"]
        self.profile_menu.configure(values=names)
        self.profile_menu.set(names[0])

    def _selected_profile(self) -> Optional[core.TunnelProfile]:
        return self._profiles_by_name.get(self.profile_menu.get())

    def _on_import_clicked(self) -> None:
        path = filedialog.askopenfilename(title="Import WireGuard .conf",
                                           filetypes=[("WireGuard config", "*.conf"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
            name = Path(path).stem
            self.engine.config_store.import_wg_conf(text, name=name)
            self._refresh_profile_list()
            self.profile_menu.set(name)
            self._hide_banner()
            self.issue_log.add(f"Imported profile '{name}'.")
        except core.TunnelPulseError as exc:
            messagebox.showerror("Import failed", exc.user_message)
        except OSError as exc:
            messagebox.showerror("Import failed", f"Couldn't read that file.\n{exc}")

    # ---- connect / disconnect (always off the main thread) ----------------

    def _on_connect_clicked(self) -> None:
        if self._busy:
            return
        if self.engine.active_profile:
            self._run_async(self.engine.disconnect, on_done=self._after_disconnect)
            return
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo("No profile selected", "Import a WireGuard .conf file first.")
            return
        self._set_busy(True, "Connecting\u2026")
        self._run_async(lambda: self.engine.connect(profile), on_done=self._after_connect)

    def _after_connect(self, error: Optional[Exception]) -> None:
        self._set_busy(False)
        if error is None:
            self._connect_start = time.time()
            self.connect_btn.configure(text="Disconnect", fg_color=Theme.DANGER)
            return

        self.connect_btn.configure(text="Connect", fg_color=Theme.ACCENT)

        if isinstance(error, core.PrivilegeError):
            self._offer_elevation()
            return

        msg = error.user_message if isinstance(error, core.TunnelPulseError) else str(error)
        self.issue_log.add(msg, severity="critical")
        messagebox.showerror("Connection failed", msg)

    def _offer_elevation(self) -> None:
        """The one case connect() can fail on that the app can actually
        fix itself: missing admin rights. Ask once, then relaunch the
        whole process elevated rather than leaving the user stuck on a
        dead-end error message with no path forward."""
        want_elevate = messagebox.askyesno(
            "Administrator rights needed",
            "Starting a tunnel installs a network adapter and firewall rules, "
            "which needs administrator rights.\n\nRelaunch TunnelPulse as administrator now?",
        )
        if not want_elevate:
            self.issue_log.add("Admin rights declined — connect again when ready.", "warning")
            return
        try:
            if core.PrivilegeManager.relaunch_as_admin():
                # A new elevated process is starting up; this one's done.
                self.engine.shutdown()
                self.destroy()
                sys.exit(0)
            else:
                messagebox.showerror("Elevation cancelled", "The administrator prompt was cancelled or dismissed.")
        except core.PrivilegeError as exc:
            messagebox.showerror("Elevation failed", exc.user_message)

    def _after_disconnect(self, error: Optional[Exception]) -> None:
        self._set_busy(False)
        self._connect_start = None
        self.connect_btn.configure(text="Connect", fg_color=Theme.ACCENT)
        self.card_download.update_value("\u2014")
        self.card_upload.update_value("\u2014")
        self.card_uptime.update_value("\u2014")
        self.card_protection.update_value("\u2014", "")

    def _on_verify_clicked(self) -> None:
        self._run_async(self.engine.verify_protection, on_done=self._after_verify)

    def _after_verify(self, result) -> None:
        error, status = (result, None) if isinstance(result, Exception) else (None, result)
        if status is None:
            return
        tone = Theme.SUCCESS if status.protected else Theme.DANGER
        subtitle = status.current_ip or "Couldn't reach an IP-check service"
        self.card_protection.update_value(status.label, subtitle, tone=tone)

    def _on_optimize_clicked(self) -> None:
        if not self.engine.active_profile:
            messagebox.showinfo("Not connected", "Connect first, then optimize the link.")
            return
        self._run_async(self.engine.optimize_link, on_done=self._after_optimize)

    def _after_optimize(self, result) -> None:
        if isinstance(result, Exception) or result is None:
            self.issue_log.add("Link optimization could not determine an MTU.", "warning")
            return
        self.card_link.update_value(f"MTU {result}", "Suggested for current network")
        self.issue_log.add(f"Optimal MTU probed: {result}. Reconnect to apply.")

    # ---- generic threaded call helper --------------------------------------

    def _run_async(self, fn: Callable, on_done: Callable[[object], None]) -> None:
        """Runs fn() on a worker thread. Deliberately does NOT call any Tk
        method (e.g. self.after) from that thread — Tkinter assumes only
        the main thread ever touches the interpreter, and calling in from
        elsewhere is a real, timing-dependent source of crashes, not just
        a theoretical one. Results are handed off through a plain
        thread-safe queue and picked up by the existing main-thread poll
        loop (_poll_events), which already runs on a normal .after() timer."""

        def worker():
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - surfaced to caller, never crashes the thread silently
                result = exc
            self._callback_queue.put((on_done, result))
        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool, label: str = "Connect") -> None:
        self._busy = busy
        self.connect_btn.configure(state="disabled" if busy else "normal",
                                    text=label if busy else self.connect_btn.cget("text"))

    # ---- polling loops ------------------------------------------------------

    def _poll_events(self) -> None:
        for event in self.engine.bus.drain():
            self._handle_event(event)
        self._drain_callbacks()
        self.after(self.POLL_INTERVAL_MS, self._poll_events)

    def _drain_callbacks(self) -> None:
        while True:
            try:
                on_done, result = self._callback_queue.get_nowait()
            except queue.Empty:
                break
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001 - one bad callback must not kill the poll loop
                self.issue_log.add(f"Internal error: {exc}", "critical")

    def _handle_event(self, event: core.TunnelEvent) -> None:
        if event.type == core.EventType.STATE_CHANGED:
            state: core.ConnectionState = event.payload["state"]
            self.status_pill.set_state(state)
            if state == core.ConnectionState.CONNECTED:
                self._hide_banner()
        elif event.type == core.EventType.METRICS_TICK:
            sample: core.BandwidthSample = event.payload["sample"]
            self.card_download.update_value(f"{sample.down_mbps:.1f} Mbps")
            self.card_upload.update_value(f"{sample.up_mbps:.1f} Mbps")
            self.card_data.update_value(f"{sample.total_down_mb + sample.total_up_mb:.0f} MB",
                                         f"\u2193 {sample.total_down_mb:.0f} MB  \u2191 {sample.total_up_mb:.0f} MB")
            self.sparkline.push(sample.down_mbps, sample.up_mbps)
        elif event.type in (core.EventType.ISSUE_DETECTED, core.EventType.ISSUE_RESOLVED):
            issue: core.Issue = event.payload["issue"]
            self.issue_log.add(issue.message, issue.severity)

    def _tick_uptime(self) -> None:
        if self._connect_start:
            elapsed = int(time.time() - self._connect_start)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.card_uptime.update_value(f"{h:02d}:{m:02d}:{s:02d}")

            if time.time() - self._last_protection_check > self.PROTECTION_RECHECK_SECS:
                self._last_protection_check = time.time()
                self._run_async(self.engine.verify_protection, on_done=self._after_verify)

        self.after(1000, self._tick_uptime)

    # ---- shutdown -------------------------------------------------------------

    def _on_close(self) -> None:
        self.connect_btn.configure(state="disabled")
        self.status_pill.set_state(core.ConnectionState.DISCONNECTING)

        def shutdown_and_exit():
            try:
                self.engine.shutdown()
            finally:
                self.after(0, self.destroy)

        threading.Thread(target=shutdown_and_exit, daemon=True).start()


# ---- ENTRY POINT  (PyInstaller target — self-healing top-level guard) ----

def _install_global_exception_guard(app: TunnelPulseApp, log) -> None:
    """A single unhandled exception in a callback must never take the
    whole app down silently — log it, tell the user, keep the window
    alive."""

    def handler(exc_type, exc_value, exc_tb):
        log.error("Unhandled GUI exception:\n%s", "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)))
        try:
            app.issue_log.add(f"Internal error: {exc_value}", "critical")
        except Exception:  # noqa: BLE001 - the error handler itself must never raise
            pass

    app.report_callback_exception = handler


def main() -> int:
    parser = argparse.ArgumentParser(prog="TunnelPulse")
    parser.add_argument("--debug", action="store_true", help="verbose logging + console output")
    args = parser.parse_args()

    log = core.configure_logging(debug=args.debug)
    log.info("TunnelPulse starting (debug=%s)", args.debug)

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    try:
        app = TunnelPulseApp(debug=args.debug)
    except Exception:
        log.critical("Fatal error during startup:\n%s", traceback.format_exc())
        try:
            messagebox.showerror("TunnelPulse", "TunnelPulse failed to start. See logs for details.")
        except Exception:  # noqa: BLE001 - even the error dialog can fail headlessly
            pass
        return 1

    _install_global_exception_guard(app, log)

    try:
        app.mainloop()
    except Exception:
        log.critical("Fatal error in main loop:\n%s", traceback.format_exc())
        return 1
    finally:
        try:
            app.engine.shutdown()
        except Exception:  # noqa: BLE001 - best-effort cleanup on the way out
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
