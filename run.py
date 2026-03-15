import os
import socket

from app import app


def _find_available_port(start_port, max_offset=20):
    for port in range(start_port, start_port + max_offset + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    requested_port = int(os.getenv("FLASK_PORT", "5000"))
    port = _find_available_port(requested_port)
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    if port != requested_port:
        print(f"[info] Port {requested_port} occupé, utilisation du port {port}.")

    app.run(host=host, port=port, debug=debug)
