"""
PhotoGuard FastAPI backend — local storage, no cloud account required.

Run:
    cd backend
    pip install -r requirements.txt
    uvicorn api:app --port 3000 --reload

Everything is stored under backend/data/ (SQLite database, image files and a
server secret). Delete that folder to reset the app.

Security model (short version):
  * Passwords are hashed with scrypt (salted, slow) — never stored in plain text.
  * Login returns a random bearer token; every vault endpoint requires it and
    only ever touches images owned by that user.
  * Images are not public. The browser loads them through HMAC-signed URLs, and
    "share" creates a separate random link that "revoke" really deletes.
  * Uploads are size-limited, must decode as a real JPEG/PNG/WebP image, and are
    re-encoded, which strips EXIF metadata such as GPS location.
  * Every upload gets an invisible DCT watermark naming its owner, and a SHA-256
    fingerprint recorded as proof of creation. /verify checks both.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from DCT import capacity_bytes, embed_payload, extract_payload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("PHOTOGUARD_DATA_DIR", Path(__file__).parent / "data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "photoguard.db"
SECRET_PATH = DATA_DIR / "secret.key"

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000  # refuse "decompression bomb" images
SESSION_TTL = dt.timedelta(days=7)
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 5 * 60

FRONTEND_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHARE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

FILES_DIR.mkdir(parents=True, exist_ok=True)

if not SECRET_PATH.exists():
    SECRET_PATH.write_bytes(secrets.token_bytes(32))
    os.chmod(SECRET_PATH, 0o600)
SECRET = SECRET_PATH.read_bytes()

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username   TEXT PRIMARY KEY,
    full_name  TEXT NOT NULL,
    pw_hash    BLOB NOT NULL,
    salt       BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS images (
    id               TEXT PRIMARY KEY,
    owner            TEXT NOT NULL REFERENCES users(username),
    name             TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    sha256           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    invisible_text   TEXT,
    visible_text     TEXT,
    metadata_removed TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS images_owner ON images(owner);
CREATE INDEX IF NOT EXISTS images_sha ON images(sha256);
CREATE TABLE IF NOT EXISTS shares (
    token      TEXT PRIMARY KEY,
    image_id   TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


with closing(db()) as _conn:
    _conn.executescript(SCHEMA)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="PhotoGuard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Passwords + sessions
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def token_digest(token: str) -> str:
    # Only a hash of each session token is stored, so a leaked database
    # can't be used to log in as anyone.
    return hashlib.sha256(token.encode()).hexdigest()


_failed_logins: dict[str, list[float]] = {}


def _recent_failures(username: str) -> list[float]:
    cutoff = time.monotonic() - LOGIN_LOCKOUT_SECONDS
    attempts = [t for t in _failed_logins.get(username, []) if t > cutoff]
    _failed_logins[username] = attempts
    return attempts


def current_user(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Please sign in.")
    token = authorization.split(" ", 1)[1].strip()
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM sessions WHERE token_hash = ?",
            (token_digest(token),),
        ).fetchone()
    if not row or dt.datetime.fromisoformat(row["expires_at"]) < now():
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return row["username"]


class SignUpBody(BaseModel):
    fullName: Optional[str] = Field(default=None, max_length=80)
    username: str
    password: str = Field(max_length=200)


class LoginBody(BaseModel):
    username: str
    password: str = Field(max_length=200)


@app.post("/auth/signup")
async def signup(body: SignUpBody):
    username = body.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="Usernames are 3–32 characters: letters, numbers, dot, dash or underscore.",
        )
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Use a password with at least 8 characters.")

    full_name = (body.fullName or username).strip() or username
    salt = secrets.token_bytes(16)
    try:
        with closing(db()) as conn, conn:
            conn.execute(
                "INSERT INTO users (username, full_name, pw_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, full_name, hash_password(body.password, salt), salt, now().isoformat()),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That username is already taken.")
    return {"user": {"username": username, "fullName": full_name}}


@app.post("/auth/login")
async def login(body: LoginBody):
    username = body.username.strip()
    if len(_recent_failures(username)) >= LOGIN_MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-in attempts. Wait 5 minutes and try again.",
        )

    with closing(db()) as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    # Hash even when the user doesn't exist, so response timing doesn't reveal
    # which usernames are registered.
    salt = user["salt"] if user else b"\0" * 16
    candidate = hash_password(body.password, salt)
    if not user or not hmac.compare_digest(candidate, user["pw_hash"]):
        _failed_logins.setdefault(username, []).append(time.monotonic())
        raise HTTPException(status_code=401, detail="That username or password is incorrect.")

    _failed_logins.pop(username, None)
    token = secrets.token_urlsafe(32)
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now().isoformat(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, username, expires_at) VALUES (?, ?, ?)",
            (token_digest(token), username, (now() + SESSION_TTL).isoformat()),
        )
    return {"token": token, "user": {"username": username, "fullName": user["full_name"]}}


@app.post("/auth/logout")
async def logout(authorization: Optional[str] = Header(default=None)):
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        with closing(db()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))
    return {"status": "signed out"}


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def image_path(image_id: str) -> Path:
    # IDs are always server-generated UUIDs; validating keeps paths inside FILES_DIR.
    if not UUID_RE.match(image_id):
        raise HTTPException(status_code=404, detail="Image not found.")
    return FILES_DIR / f"{image_id}.png"


def owned_image(image_id: str, username: str) -> sqlite3.Row:
    image_path(image_id)
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT * FROM images WHERE id = ? AND owner = ?", (image_id, username)
        ).fetchone()
    if not row:
        # Same answer whether the image is missing or belongs to someone else.
        raise HTTPException(status_code=404, detail="Image not found.")
    return row


def file_signature(image_id: str, sha256: str) -> str:
    return hmac.new(SECRET, f"{image_id}:{sha256}".encode(), hashlib.sha256).hexdigest()[:32]


def base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def file_url(request: Request, row: sqlite3.Row) -> str:
    sig = file_signature(row["id"], row["sha256"])
    return f"{base_url(request)}/files/{row['id']}?sig={sig}"


def share_url(request: Request, token: str) -> str:
    return f"{base_url(request)}/s/{token}"


def human_size(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def serialize(request: Request, row: sqlite3.Row) -> dict:
    with closing(db()) as conn:
        share = conn.execute(
            "SELECT token FROM shares WHERE image_id = ? ORDER BY created_at DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
    return {
        "id": row["id"],
        "imageId": row["id"],
        "url": file_url(request, row),
        "name": row["name"],
        "size": human_size(row["size_bytes"]),
        "uploadedAt": row["created_at"],
        "owner": row["owner"],
        "watermarked": bool(row["visible_text"]),
        "visibleWatermark": row["visible_text"],
        "invisibleWatermark": row["invisible_text"],
        "hashFingerprint": row["sha256"],
        "metadataRemoved": [m for m in row["metadata_removed"].split(",") if m],
        "shared": share is not None,
        "shareUrl": share_url(request, share["token"]) if share else None,
    }


def save_image(image_id: str, img: np.ndarray) -> tuple[int, str]:
    ok, buf = cv2.imencode(".png", img)  # lossless, so the hidden mark survives storage
    if not ok:
        raise HTTPException(status_code=500, detail="Could not encode image.")
    data = buf.tobytes()
    image_path(image_id).write_bytes(data)
    return len(data), hashlib.sha256(data).hexdigest()


def load_image(image_id: str) -> np.ndarray:
    img = cv2.imread(str(image_path(image_id)), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=404, detail="Image file is missing.")
    return img


def apply_invisible(img: np.ndarray, image_id: str, text: str) -> tuple[np.ndarray, Optional[str]]:
    # The first 8 characters of the image ID let /verify find the original
    # record even after the file has been re-saved or recompressed.
    payload = f"{image_id[:8]}|{text}"
    if len(payload.encode()) > capacity_bytes(img):
        return img, None
    return embed_payload(img, payload), text


SENSITIVE_EXIF = {
    0x8825: "GPS location",
    0x010F: "Camera make",
    0x0110: "Camera model",
    0x9003: "Date taken",
    0xA431: "Camera serial number",
    0x013B: "Artist name",
    0x0131: "Editing software",
}


def sensitive_metadata(data: bytes) -> list[str]:
    """List privacy-sensitive EXIF fields present in the original upload."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            exif = im.getexif()
            tags = set(exif.keys())
            if 0x8769 in exif:  # Exif sub-IFD holds "date taken" and serial number
                tags |= set(exif.get_ifd(0x8769).keys())
            return [label for tag, label in SENSITIVE_EXIF.items() if tag in tags]
    except Exception:
        return []


def decode_upload(data: bytes) -> np.ndarray:
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Images must be 15 MB or smaller.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt, pixels = im.format, im.width * im.height
    except Exception:
        raise HTTPException(status_code=415, detail="That file isn't a readable image.")
    if fmt not in {"JPEG", "PNG", "WEBP"}:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG and WebP images are supported.")
    if pixels > MAX_PIXELS:
        raise HTTPException(status_code=413, detail="That image has too many pixels.")

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=415, detail="That file isn't a readable image.")
    return img


def safe_name(filename: Optional[str]) -> str:
    stem = Path(filename or "image").stem
    stem = re.sub(r"[^\w .-]", "", stem).strip()[:80] or "image"
    return f"{stem}.png"


# ---------------------------------------------------------------------------
# Vault endpoints
# ---------------------------------------------------------------------------

@app.get("/images")
async def list_images(request: Request, username: str = Depends(current_user)):
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT * FROM images WHERE owner = ? ORDER BY created_at", (username,)
        ).fetchall()
    return {"images": [serialize(request, r) for r in rows]}


@app.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    username: str = Depends(current_user),
):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    img = decode_upload(data)
    removed = sensitive_metadata(data)

    image_id = str(uuid.uuid4())
    img, invisible = apply_invisible(img, image_id, f"© {username}")
    size, sha = save_image(image_id, img)  # re-encoding drops all EXIF metadata

    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO images (id, owner, name, size_bytes, sha256, created_at, invisible_text, metadata_removed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (image_id, username, safe_name(file.filename), size, sha, now().isoformat(),
             invisible, ",".join(removed)),
        )
        row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    return serialize(request, row)


class WatermarkSettings(BaseModel):
    type: str = "invisible"
    text: Optional[str] = Field(default="© PROTECTED", max_length=60)
    fontFamily: Optional[str] = "Arial"
    position: Optional[str] = "center"
    opacity: Optional[int] = Field(default=50, ge=0, le=100)
    size: Optional[int] = Field(default=40, ge=8, le=400)
    customX: Optional[float] = Field(default=50, ge=0, le=100)
    customY: Optional[float] = Field(default=50, ge=0, le=100)


class WatermarkRequest(BaseModel):
    imageId: str
    settings: WatermarkSettings


def draw_visible(img: np.ndarray, s: WatermarkSettings) -> np.ndarray:
    """Overlay visible text with Pillow (unlike cv2.putText it renders ©, accents, etc.)."""
    base = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
    w, h = base.size
    text = (s.text or "").strip() or "© PROTECTED"
    # The customizer's size is tuned for a ~800px preview; scale to the real image.
    font_px = max(10, int((s.size or 40) * max(w, h) / 800))
    font = ImageFont.load_default(size=font_px)
    alpha = int(255 * (50 if s.opacity is None else s.opacity) / 100)

    def stamp(angle: float) -> Image.Image:
        left, top, right, bottom = font.getbbox(text)
        pad = max(2, font_px // 4)
        tile = Image.new("RGBA", (right - left + 2 * pad, bottom - top + 2 * pad), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        shadow = max(1, font_px // 20)
        d.text((pad - left + shadow, pad - top + shadow), text, font=font, fill=(0, 0, 0, alpha // 2))
        d.text((pad - left, pad - top), text, font=font, fill=(255, 255, 255, alpha))
        return tile.rotate(angle, expand=True, resample=Image.BICUBIC) if angle else tile

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))

    def place(tile: Image.Image, cx: float, cy: float):
        layer.paste(tile, (int(cx - tile.width / 2), int(cy - tile.height / 2)), tile)

    position = s.position or "center"
    if position == "tiled":
        tile = stamp(30)
        step_x, step_y = tile.width + font_px, tile.height + font_px
        for row, y in enumerate(range(0, h + step_y, step_y)):
            offset = (step_x // 2) * (row % 2)
            for x in range(-offset, w + step_x, step_x):
                place(tile, x, y)
    elif position == "corner":
        tile = stamp(0)
        margin = font_px // 2
        place(tile, w - tile.width / 2 - margin, h - tile.height / 2 - margin)
    elif position == "custom":
        place(stamp(30), w * (s.customX or 50) / 100, h * (s.customY or 50) / 100)
    else:
        place(stamp(30), w / 2, h / 2)

    out = Image.alpha_composite(base, layer).convert("RGB")
    return cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)


@app.post("/watermark")
async def watermark(request: Request, body: WatermarkRequest, username: str = Depends(current_user)):
    row = owned_image(body.imageId, username)
    img = load_image(row["id"])
    s = body.settings
    visible_text = row["visible_text"]
    invisible_text = row["invisible_text"]

    if s.type == "visible":
        img = draw_visible(img, s)
        visible_text = (s.text or "").strip() or "© PROTECTED"
        # Drawing on top can overwrite the hidden mark, so write it again.
        img, invisible_text = apply_invisible(img, row["id"], invisible_text or f"© {username}")
    elif s.type == "invisible":
        img, invisible_text = apply_invisible(img, row["id"], (s.text or "").strip() or f"© {username}")
        if invisible_text is None:
            raise HTTPException(status_code=422, detail="Image is too small to hold that hidden watermark.")
    else:
        raise HTTPException(status_code=422, detail="Watermark type must be 'visible' or 'invisible'.")

    size, sha = save_image(row["id"], img)
    with closing(db()) as conn, conn:
        conn.execute(
            "UPDATE images SET size_bytes = ?, sha256 = ?, visible_text = ?, invisible_text = ? WHERE id = ?",
            (size, sha, visible_text, invisible_text, row["id"]),
        )
        row = conn.execute("SELECT * FROM images WHERE id = ?", (row["id"],)).fetchone()
    result = serialize(request, row)
    result["watermarkedUrl"] = result["url"]
    return result


@app.get("/files/{image_id}")
async def serve_file(image_id: str, sig: str = ""):
    path = image_path(image_id)
    with closing(db()) as conn:
        row = conn.execute("SELECT id, sha256 FROM images WHERE id = ?", (image_id,)).fetchone()
    if not row or not hmac.compare_digest(sig, file_signature(row["id"], row["sha256"])):
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(path.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/image/{image_id}/download")
async def download(image_id: str, username: str = Depends(current_user)):
    row = owned_image(image_id, username)
    name = f"protected-{row['name']}" if row["visible_text"] else row["name"]
    return Response(
        image_path(image_id).read_bytes(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.delete("/image/{image_id}")
async def delete_image(image_id: str, username: str = Depends(current_user)):
    owned_image(image_id, username)
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
    image_path(image_id).unlink(missing_ok=True)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Sharing — a separate random link per share, deleted on revoke
# ---------------------------------------------------------------------------

@app.post("/image/{image_id}/share")
async def share_image(request: Request, image_id: str, username: str = Depends(current_user)):
    owned_image(image_id, username)
    token = secrets.token_urlsafe(18)
    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO shares (token, image_id, created_at) VALUES (?, ?, ?)",
            (token, image_id, now().isoformat()),
        )
    return {"shareUrl": share_url(request, token)}


@app.post("/image/{image_id}/revoke")
async def revoke_image(image_id: str, username: str = Depends(current_user)):
    owned_image(image_id, username)
    with closing(db()) as conn, conn:
        revoked = conn.execute("DELETE FROM shares WHERE image_id = ?", (image_id,)).rowcount
    return {"status": "revoked", "imageId": image_id, "linksRevoked": revoked}


@app.get("/s/{token}")
async def open_share(token: str):
    if SHARE_RE.match(token):
        with closing(db()) as conn:
            row = conn.execute("SELECT image_id FROM shares WHERE token = ?", (token,)).fetchone()
        if row:
            return Response(image_path(row["image_id"]).read_bytes(), media_type="image/png",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="This link is invalid or has been revoked.")


# ---------------------------------------------------------------------------
# Proof of creation + verification
# ---------------------------------------------------------------------------

@app.get("/image/{image_id}/proof")
async def proof_of_creation(image_id: str, username: str = Depends(current_user)):
    row = owned_image(image_id, username)
    return {"hash": row["sha256"], "createdAt": row["created_at"], "owner": row["owner"]}


@app.post("/verify")
async def verify(file: UploadFile = File(...)):
    """
    Check any image for PhotoGuard protection. Public on purpose: anyone who
    finds a copy of a photo online can check who it belongs to.
    """
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    img = decode_upload(data)
    sha = hashlib.sha256(data).hexdigest()

    with closing(db()) as conn:
        exact = conn.execute(
            "SELECT owner, created_at FROM images WHERE sha256 = ?", (sha,)
        ).fetchone()

        mark = None
        payload = extract_payload(img)
        if payload:
            prefix, sep, text = payload.partition("|")
            match = None
            if sep and re.fullmatch(r"[0-9a-f]{8}", prefix):
                match = conn.execute(
                    "SELECT owner, created_at FROM images WHERE id LIKE ?", (prefix + "-%",)
                ).fetchone()
            mark = {
                "text": text if sep else payload,
                "registeredOwner": match["owner"] if match else None,
                "registeredAt": match["created_at"] if match else None,
            }

    return {
        "sha256": sha,
        "exactMatch": {"owner": exact["owner"], "registeredAt": exact["created_at"]} if exact else None,
        "watermark": mark,
        "protected": bool(exact or mark),
    }
