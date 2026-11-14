#!/usr/bin/env python3
"""Generate a daily OS schedule for this machine; do not activate it."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import shlex
import sys

import vw_vn_stock as a


def systemd_quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def task_name_for_config(cfg):
    codes = a.selected_fuel_codes(cfg)
    return "vw-vn-stock" if codes == ["B", "D"] else "vw-vn-stock-" + "-".join(c.lower() for c in codes)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--platform", choices=("linux", "macos"), required=True)
    p.add_argument("--time", default="07:45", help="HH:MM; ejemplo por defecto, modificable")
    p.add_argument("--config", type=Path, default=a.ROOT / "config.json")
    args = p.parse_args()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", args.time):
        p.error("Hora inválida; usa HH:MM.")
    cfg = a.load_config(args.config)
    task_name = task_name_for_config(cfg)
    hour, minute = map(int, args.time.split(":"))
    folder = a.ROOT / "schedule_generated"
    folder.mkdir(exist_ok=True)
    command = [sys.executable, str(a.ROOT / "vw_vn_stock.py"), "run", "--config", str(args.config.resolve())]
    if args.platform == "linux":
        service = "\n".join([
            "[Unit]", "Description=Volkswagen new stock daily history", "", "[Service]", "Type=oneshot",
            "WorkingDirectory=" + str(a.ROOT).replace("%", "%%"),
            "ExecStart=" + " ".join(systemd_quote(s) for s in command),
            "TimeoutStartSec=45min", "UMask=0077", "NoNewPrivileges=true", "",
        ])
        timer = "\n".join([
            "[Unit]", "Description=Daily Volkswagen stock capture", "", "[Timer]",
            f"OnCalendar=*-*-* {args.time}:00 {cfg['timezone']}",
            "Persistent=true", "AccuracySec=1min", "", "[Install]", "WantedBy=timers.target", "",
        ])
        (folder / (task_name + ".service")).write_text(service)
        (folder / (task_name + ".timer")).write_text(timer)
        print("Archivos generados. Para activar en este Linux tras validar una captura manual:")
        print("mkdir -p ~/.config/systemd/user")
        print("cp " + shlex.quote(str(folder / (task_name + ".service"))) + " " + shlex.quote(str(folder / (task_name + ".timer"))) + " ~/.config/systemd/user/")
        print("systemctl --user daemon-reload")
        print("systemctl --user enable --now " + task_name + ".timer")
        print("systemctl --user list-timers " + task_name + ".timer")
        print("Para funcionar sin sesión abierta, el administrador debe habilitar linger para este usuario.")
    else:
        logs = Path(cfg["data_dir"]) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        settings = {
            "Label": "es.local." + task_name,
            "ProgramArguments": command,
            "WorkingDirectory": str(a.ROOT),
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "StandardOutPath": str(logs / (task_name + ".log")),
            "StandardErrorPath": str(logs / (task_name + "-error.log")),
        }
        target = folder / ("es.local." + task_name + ".plist")
        target.write_bytes(plistlib.dumps(settings))
        print("Archivo generado. La hora de launchd usa la zona horaria LOCAL del Mac.")
        print("Configura el Mac en " + cfg["timezone"] + "; el equipo debe estar disponible y la sesión iniciada.")
        print("Para activar en este Mac tras validar una captura manual:")
        print("mkdir -p ~/Library/LaunchAgents")
        print("cp " + shlex.quote(str(target)) + " ~/Library/LaunchAgents/")
        print('launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/es.local.' + task_name + '.plist')
        print('launchctl print "gui/$(id -u)/es.local.' + task_name + '"')
    print("Todavía no se ha activado ninguna tarea.")


if __name__ == "__main__":
    main()
