import atexit
import os
import socket
import subprocess
import threading
import time

from app import app


_SHAIRPORT_LOG_PROCESS = None


def _get_bool_env(name, default="false"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _relay_process_output(prefix, process):
    if process.stdout is None:
        return

    for line in process.stdout:
        text = line.rstrip()
        if text:
            print(f"[{prefix}] {text}")


def _stop_shairport_log_relay():
    global _SHAIRPORT_LOG_PROCESS
    process = _SHAIRPORT_LOG_PROCESS
    if process is None:
        return

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()

    _SHAIRPORT_LOG_PROCESS = None


def _start_shairport_log_relay():
    global _SHAIRPORT_LOG_PROCESS

    enabled = _get_bool_env("SHAIRPORT_LOG_TO_SERVER_CONSOLE", "true")
    if not enabled:
        return

    service_name = os.getenv("SHAIRPORT_SYNC_SERVICE", "shairport-sync")
    use_sudo = _get_bool_env("SHAIRPORT_SYNC_USE_SUDO", "false")
    base_command = ["journalctl", "-fu", service_name, "-n", "20", "--no-pager", "-o", "cat"]
    commands = []
    if use_sudo:
        commands.append(["sudo", "-n", *base_command])
    commands.append(base_command)

    process = None
    startup_error = None
    for command in commands:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=os.environ.copy(),
            )
            time_limit = 0.2
            start = time.monotonic()
            while process.poll() is None and (time.monotonic() - start) < time_limit:
                time.sleep(0.02)
            if process.poll() is not None:
                output = ""
                if process.stdout is not None:
                    output = process.stdout.read().strip()
                startup_error = output or f"commande échouée ({command[0]})"
                process = None
                continue
            break
        except FileNotFoundError:
            startup_error = "journalctl introuvable"
        except Exception as exc:
            startup_error = str(exc)

    if process is None:
        if startup_error:
            print(f"[warn] Relais des logs shairport-sync indisponible: {startup_error}")
        else:
            print("[warn] Relais des logs shairport-sync indisponible.")
        return

    _SHAIRPORT_LOG_PROCESS = process

    threading.Thread(
        target=_relay_process_output,
        args=("shairport-sync", _SHAIRPORT_LOG_PROCESS),
        daemon=True,
        name="shairport-sync-log-relay",
    ).start()
    atexit.register(_stop_shairport_log_relay)
    print(f"[info] Relais logs activé pour le service systemd '{service_name}'.")


def _find_available_port(start_port, max_offset=20):
    for port in range(start_port, start_port + max_offset + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    requested_port = int(os.getenv("FLASK_PORT", "5001"))
    port = _find_available_port(requested_port)
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    ssl_enabled = os.getenv("FLASK_SSL", "true").lower() == "true"

    # In debug mode with the Werkzeug reloader, only start background helpers once.
    should_start_helpers = (not debug) or os.getenv("WERKZEUG_RUN_MAIN") == "true"
    if should_start_helpers:
        _start_shairport_log_relay()

    if port != requested_port:
        print(f"[info] Port {requested_port} occupé, utilisation du port {port}.")

    if ssl_enabled:
        print(f"[info] HTTPS activé (certificat auto-signé).")
        print(f"[info] Ouvrir: https://127.0.0.1:{port} ou https://{host}:{port}")
        app.run(host=host, port=port, debug=debug, ssl_context="adhoc")
    else:
        print("[info] HTTPS désactivé (certaines API navigateur seront indisponibles).")
        app.run(host=host, port=port, debug=debug)
