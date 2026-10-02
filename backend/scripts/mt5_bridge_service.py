"""Windows service wrapper for the MT5 HTTP bridge.

The bridge used to be started by hand from a .bat file, which meant it died with
the console window and — worse — the trading container fell back to the simulator
and kept trading fake data with no visible error. Running it as a service with
automatic restart removes that failure mode: the port comes back on its own, and
the container's reconnect supervisor picks it up.

Installed via install_service.py (pywin32). Runs the same bridge module the
manual .bat used, with the identical environment.
"""
from __future__ import annotations

import os
import sys
import time

import servicemanager
import win32api
import win32event
import win32service
import win32serviceutil

REPO = r"E:\forex\aiagent\backend"
TOKEN_FILE = r"C:\Users\HP\workspace\e2e_backup\bridge_token.txt"
Tailscale_IP = "100.69.186.8"
PORT = "8765"
VPS_IP = "100.94.237.27"

_stop_event = win32event.CreateEvent(None, 0, 0, None)


class MT5BridgeService(win32serviceutil.ServiceFramework):
    _svc_name_ = "TitanMT5Bridge"
    _svc_display_name_ = "TITAN MT5 HTTP Bridge"
    _svc_description_ = ("Serves the MT5 JSON-RPC bridge on the Tailscale interface "
                         "so the VPS trading container can read the real terminal.")

    def __init__(self, args):
        super().__init__(args)
        self._proc = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(_stop_event)
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""))
        try:
            self.main()
        finally:
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, ""))

    def main(self):
        # Wait a little at boot: Tailscale may not have its address yet, and the
        # bridge binds specifically to the Tailscale IP.
        time.sleep(20)
        while True:
            rc = win32event.WaitForSingleObject(_stop_event, 1000)
            if rc == win32event.WAIT_OBJECT_0:
                return
            if self._proc is None or self._proc.poll() is not None:
                if self._proc is not None:
                    servicemanager.LogErrorMsg(
                        f"bridge exited rc={self._proc.returncode}; restarting in 5s")
                    time.sleep(5)
                if rc == win32event.WAIT_OBJECT_0:
                    return
                self._proc = self._spawn()
            time.sleep(1)

    def _spawn(self):
        import subprocess
        env = dict(os.environ)
        try:
            with open(TOKEN_FILE, encoding="utf-8") as fh:
                env["MT5_BRIDGE_AUTH_TOKEN"] = fh.read().strip()
        except OSError:
            servicemanager.LogErrorMsg(f"cannot read token file {TOKEN_FILE}")
        env["MT5_BRIDGE_HTTP_HOST"] = Tailscale_IP
        env["MT5_BRIDGE_HTTP_PORT"] = PORT
        env["MT5_BRIDGE_ALLOW_TRADE"] = "true"
        env["MT5_BRIDGE_ALLOWED_IPS"] = VPS_IP
        env["PYTHONPATH"] = REPO
        log = open(r"C:\Users\HP\workspace\e2e_backup\bridge_service.log", "ab")
        return subprocess.Popen(
            [sys.executable, "-m", "app.mt5.http_bridge"],
            cwd=REPO, env=env, stdout=log, stderr=log)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(MT5BridgeService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(MT5BridgeService)
