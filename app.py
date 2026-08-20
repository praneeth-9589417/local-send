"""Local Send — a LAN-only, room-based file sharing server."""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import shutil
import socket
import sqlite3
import string
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LOCAL_SEND_DATA_DIR", BASE_DIR / "data"))
UPLOADS_DIR = DATA_DIR / "uploads"
DATABASE_PATH = DATA_DIR / "local_send.db"
PORT = int(os.environ.get("LOCAL_SEND_PORT", "8080"))
MAX_UPLOAD_MB = int(os.environ.get("LOCAL_SEND_MAX_UPLOAD_MB", "2048"))
ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ACCESS_MODES = {"collaborative", "owner_only"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


@app.after_request
def keep_interface_fresh(response: Response) -> Response:
    """Avoid a browser using an old interface after the host is updated."""
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS rooms (
                code TEXT PRIMARY KEY,
                owner_token TEXT NOT NULL,
                access_mode TEXT NOT NULL DEFAULT 'collaborative',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                room_code TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                FOREIGN KEY (room_code) REFERENCES rooms(code) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS files_room_code_idx ON files(room_code);
            """
        )
        room_columns = {row["name"] for row in connection.execute("PRAGMA table_info(rooms)")}
        if "access_mode" not in room_columns:
            # Rooms made by the earlier MVP remain usable and collaborative.
            connection.execute(
                "ALTER TABLE rooms ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'collaborative'"
            )


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def valid_room_code(value: str) -> bool:
    return len(value) == 8 and all(character in ROOM_ALPHABET for character in value)


def normalize_room_code(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def valid_access_mode(value: object) -> bool:
    return value in ACCESS_MODES


def new_room_code(connection: sqlite3.Connection) -> str:
    # A collision is extremely unlikely, but the database remains the source of truth.
    for _ in range(20):
        code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(8))
        if connection.execute("SELECT 1 FROM rooms WHERE code = ?", (code,)).fetchone() is None:
            return code
    raise RuntimeError("Could not generate a unique room code. Please try again.")


def room_payload(connection: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    room = connection.execute(
        "SELECT code, access_mode, created_at FROM rooms WHERE code = ?", (code,)
    ).fetchone()
    if room is None:
        return None
    files = connection.execute(
        """
        SELECT id, original_name, size_bytes, uploaded_at
        FROM files
        WHERE room_code = ?
        ORDER BY uploaded_at DESC, original_name COLLATE NOCASE
        """,
        (code,),
    ).fetchall()
    return {
        "code": room["code"],
        "access_mode": room["access_mode"],
        "created_at": room["created_at"],
        "files": [dict(item) for item in files],
    }


def owner_is_valid(connection: sqlite3.Connection, code: str) -> bool:
    supplied_token = request.headers.get("X-Room-Owner-Key", "")
    room = connection.execute("SELECT owner_token FROM rooms WHERE code = ?", (code,)).fetchone()
    return room is not None and bool(supplied_token) and hmac.compare_digest(room["owner_token"], supplied_token)


def clean_display_name(filename: str) -> str:
    # Files are stored under generated names. This only controls the user-facing download name.
    name = Path(filename.replace("\\", "/")).name.strip()
    return name or "unnamed-file"


def local_addresses() -> list[str]:
    """Return likely LAN addresses without needing an internet connection."""
    addresses: list[str] = []

    def remember(address: str) -> None:
        if address not in addresses:
            addresses.append(address)

    # UDP connect selects the preferred local interface but sends no traffic.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # TEST-NET; no internet service is used.
            remember(probe.getsockname()[0])
    except OSError:
        pass

    # Keep other interfaces as fallbacks, after the likely Wi-Fi/Ethernet address.
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            remember(item[4][0])
    except OSError:
        pass

    usable: list[str] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
            if not parsed.is_loopback and not parsed.is_unspecified:
                usable.append(address)
        except ValueError:
            pass
    return usable


@app.get("/")
def index() -> Response:
    return app.send_static_file("index.html")


@app.get("/api/server-info")
def server_info() -> Response:
    return jsonify(
        {
            "port": PORT,
            "addresses": [f"http://{address}:{PORT}" for address in local_addresses()],
            "max_upload_mb": MAX_UPLOAD_MB,
        }
    )


@app.post("/api/rooms")
def create_room() -> Response:
    body = request.get_json(silent=True) or {}
    access_mode = body.get("access_mode", "owner_only")
    if not valid_access_mode(access_mode):
        return jsonify({"error": "Choose either collaborative or owner-only uploads."}), 400
    with database() as connection:
        code = new_room_code(connection)
        owner_token = secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO rooms (code, owner_token, access_mode, created_at) VALUES (?, ?, ?, ?)",
            (code, owner_token, access_mode, now()),
        )
    return jsonify({"code": code, "owner_token": owner_token, "access_mode": access_mode}), 201


@app.get("/api/rooms/<code>")
def get_room(code: str) -> Response:
    code = normalize_room_code(code)
    if not valid_room_code(code):
        return jsonify({"error": "Enter a valid 8-character room code."}), 400
    with database() as connection:
        room = room_payload(connection, code)
    if room is None:
        return jsonify({"error": "This room does not exist. Check the code with its creator."}), 404
    return jsonify(room)


@app.post("/api/rooms/<code>/files")
def upload_file(code: str) -> Response:
    code = normalize_room_code(code)
    if not valid_room_code(code):
        return jsonify({"error": "Enter a valid 8-character room code."}), 400

    uploaded_file = request.files.get("file")
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify({"error": "Choose a file before uploading."}), 400

    with database() as connection:
        room = connection.execute(
            "SELECT access_mode FROM rooms WHERE code = ?", (code,)
        ).fetchone()
        if room is None:
            return jsonify({"error": "This room no longer exists."}), 404
        if room["access_mode"] == "owner_only" and not owner_is_valid(connection, code):
            return jsonify({"error": "This room is set to owner-only uploads."}), 403
        file_id = uuid4().hex
        stored_name = uuid4().hex
        room_directory = UPLOADS_DIR / code
        room_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = room_directory / f"{stored_name}.part"
        final_path = room_directory / stored_name

        try:
            uploaded_file.save(temporary_path)
            size_bytes = temporary_path.stat().st_size
            temporary_path.replace(final_path)
            connection.execute(
                """
                INSERT INTO files (id, room_code, original_name, stored_name, size_bytes, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (file_id, code, clean_display_name(uploaded_file.filename), stored_name, size_bytes, now()),
            )
        except OSError as error:
            temporary_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            return jsonify({"error": f"The server could not save this file: {error}"}), 507

    return jsonify({"id": file_id, "message": "File uploaded."}), 201


@app.patch("/api/rooms/<code>")
def update_room(code: str) -> Response:
    code = normalize_room_code(code)
    if not valid_room_code(code):
        return jsonify({"error": "Enter a valid 8-character room code."}), 400
    body = request.get_json(silent=True) or {}
    access_mode = body.get("access_mode")
    if not valid_access_mode(access_mode):
        return jsonify({"error": "Choose either collaborative or owner-only uploads."}), 400
    with database() as connection:
        if not owner_is_valid(connection, code):
            return jsonify({"error": "Only the room creator can change room settings."}), 403
        connection.execute("UPDATE rooms SET access_mode = ? WHERE code = ?", (access_mode, code))
        room = room_payload(connection, code)
    return jsonify(room)


@app.delete("/api/rooms/<code>")
def delete_room(code: str) -> Response:
    code = normalize_room_code(code)
    if not valid_room_code(code):
        return jsonify({"error": "Enter a valid 8-character room code."}), 400
    with database() as connection:
        if not owner_is_valid(connection, code):
            return jsonify({"error": "Only the room creator can delete this room."}), 403

    room_directory = UPLOADS_DIR / code
    try:
        if room_directory.exists():
            shutil.rmtree(room_directory)
    except OSError as error:
        return jsonify({"error": f"The server could not delete the room files: {error}"}), 500

    with database() as connection:
        connection.execute("DELETE FROM rooms WHERE code = ?", (code,))
    return jsonify({"message": "Room deleted."})


@app.get("/api/files/<file_id>/download")
def download_file(file_id: str) -> Response:
    with database() as connection:
        file = connection.execute(
            "SELECT room_code, original_name, stored_name FROM files WHERE id = ?", (file_id,)
        ).fetchone()
    if file is None:
        return jsonify({"error": "This file is no longer available."}), 404

    directory = UPLOADS_DIR / file["room_code"]
    if not (directory / file["stored_name"]).is_file():
        return jsonify({"error": "The file record exists, but the file is missing on the host."}), 404
    return send_from_directory(
        directory,
        file["stored_name"],
        as_attachment=True,
        download_name=file["original_name"],
        conditional=True,
        max_age=0,
    )


@app.delete("/api/files/<file_id>")
def delete_file(file_id: str) -> Response:
    with database() as connection:
        file = connection.execute(
            "SELECT room_code, stored_name FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if file is None:
            return jsonify({"error": "This file is no longer available."}), 404
        if not owner_is_valid(connection, file["room_code"]):
            return jsonify({"error": "Only the room creator can delete files."}), 403

        file_path = UPLOADS_DIR / file["room_code"] / file["stored_name"]
        try:
            file_path.unlink(missing_ok=True)
        except OSError as error:
            return jsonify({"error": f"The server could not delete this file: {error}"}), 500
        connection.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return jsonify({"message": "File deleted."})


@app.errorhandler(RequestEntityTooLarge)
def file_too_large(_error: RequestEntityTooLarge) -> tuple[Response, int]:
    return jsonify({"error": f"That file is larger than this server's {MAX_UPLOAD_MB} MB upload limit."}), 413


@app.errorhandler(404)
def api_not_found(_error: Exception) -> tuple[Response, int] | Response:
    if request.path.startswith("/api/"):
        return jsonify({"error": "That Local Send endpoint was not found."}), 404
    return app.send_static_file("index.html")


if __name__ == "__main__":
    initialize_database()
    print("\nLocal Send is running on this laptop.")
    print(f"Open locally: http://localhost:{PORT}")
    for address in local_addresses():
        print(f"Open on your LAN: http://{address}:{PORT}")
    print("Press Ctrl+C to stop the server.\n")
    # 0.0.0.0 is essential: it accepts LAN connections, not only localhost.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
