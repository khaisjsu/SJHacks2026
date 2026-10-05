"""
End-to-end tests for the PhotoGuard API.

Run from the backend folder:
    pip install -r requirements-dev.txt
    pytest
"""

import importlib
import io
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOGUARD_DATA_DIR", str(tmp_path / "data"))
    import api
    importlib.reload(api)  # pick up the fresh data dir
    return TestClient(api.app)


def photo_bytes(fmt="JPEG", size=(640, 480), with_gps=False) -> bytes:
    rng = np.random.default_rng(1)
    arr = cv2.GaussianBlur((rng.random((size[1], size[0], 3)) * 255).astype(np.uint8), (21, 21), 0)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    if with_gps:
        exif = Image.Exif()
        exif[0x010F] = "Canon"
        exif[0x0110] = "EOS R5"
        exif[0x8825] = {1: "N", 2: (37.0, 20.0, 1.0)}
        img.save(buf, fmt, exif=exif)
    else:
        img.save(buf, fmt)
    return buf.getvalue()


def sign_in(client, username="khai") -> dict:
    assert client.post("/auth/signup", json={"fullName": "Khai", "username": username, "password": PASSWORD}).status_code == 200
    r = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def upload(client, headers, data=None, name="beach.jpg"):
    r = client.post("/upload", headers=headers, files={"file": (name, data or photo_bytes(), "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()


# --- auth -------------------------------------------------------------------

def test_passwords_are_hashed_not_stored(client):
    sign_in(client)
    import api
    raw = api.DB_PATH.read_bytes()
    assert PASSWORD.encode() not in raw


def test_wrong_password_and_duplicate_username(client):
    sign_in(client)
    assert client.post("/auth/login", json={"username": "khai", "password": "nope-nope-nope"}).status_code == 401
    assert client.post("/auth/signup", json={"username": "khai", "password": PASSWORD}).status_code == 409


def test_login_lockout_after_repeated_failures(client):
    sign_in(client)
    for _ in range(5):
        client.post("/auth/login", json={"username": "khai", "password": "wrong-password"})
    r = client.post("/auth/login", json={"username": "khai", "password": PASSWORD})
    assert r.status_code == 429


def test_vault_requires_sign_in(client):
    assert client.get("/images").status_code == 401
    assert client.get("/images", headers={"Authorization": "Bearer made-up"}).status_code == 401


def test_logout_invalidates_token(client):
    headers = sign_in(client)
    client.post("/auth/logout", headers=headers)
    assert client.get("/images", headers=headers).status_code == 401


# --- upload + protection ---------------------------------------------------

def test_upload_adds_invisible_mark_and_fingerprint(client):
    headers = sign_in(client)
    img = upload(client, headers)
    assert img["invisibleWatermark"] == "© khai"
    assert len(img["hashFingerprint"]) == 64

    stored = client.get(img["url"].replace("http://testserver", ""))
    assert stored.status_code == 200

    from DCT import extract_payload
    decoded = cv2.imdecode(np.frombuffer(stored.content, np.uint8), cv2.IMREAD_COLOR)
    assert extract_payload(decoded) == f"{img['id'][:8]}|© khai"


def test_upload_strips_gps_and_camera_metadata(client):
    headers = sign_in(client)
    img = upload(client, headers, photo_bytes(with_gps=True))
    assert "GPS location" in img["metadataRemoved"]
    assert "Camera model" in img["metadataRemoved"]

    stored = client.get(img["url"].replace("http://testserver", "")).content
    assert len(Image.open(io.BytesIO(stored)).getexif()) == 0


def test_rejects_non_images(client):
    headers = sign_in(client)
    r = client.post("/upload", headers=headers,
                    files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")})
    assert r.status_code == 415


def test_files_need_a_valid_signature(client):
    headers = sign_in(client)
    img = upload(client, headers)
    assert client.get(f"/files/{img['id']}").status_code == 404
    assert client.get(f"/files/{img['id']}?sig=deadbeef").status_code == 404


def test_path_traversal_is_rejected(client):
    headers = sign_in(client)
    for bad in ["..%2F..%2Fapi", "../../secret", "*"]:
        assert client.get(f"/image/{bad}/download", headers=headers).status_code == 404


# --- isolation between users ----------------------------------------------

def test_users_cannot_touch_each_others_images(client):
    alice = sign_in(client, "alice")
    bob = sign_in(client, "bobby")
    img = upload(client, alice)

    assert client.get("/images", headers=bob).json()["images"] == []
    assert client.get(f"/image/{img['id']}/download", headers=bob).status_code == 404
    assert client.delete(f"/image/{img['id']}", headers=bob).status_code == 404
    assert client.post(f"/image/{img['id']}/share", headers=bob).status_code == 404
    assert client.post("/watermark", headers=bob,
                       json={"imageId": img["id"], "settings": {"type": "visible"}}).status_code == 404
    # Alice still has it
    assert client.get(f"/image/{img['id']}/download", headers=alice).status_code == 200


# --- watermarking ----------------------------------------------------------

@pytest.mark.parametrize("position", ["center", "corner", "tiled", "custom"])
def test_visible_watermark_keeps_id_and_hidden_mark(client, position):
    headers = sign_in(client)
    img = upload(client, headers)
    r = client.post("/watermark", headers=headers, json={
        "imageId": img["id"],
        "settings": {"type": "visible", "text": "© Khai 2026", "position": position, "opacity": 60, "size": 40},
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == img["id"]  # same record — downloads now return the protected file
    assert out["watermarked"] is True
    assert out["hashFingerprint"] != img["hashFingerprint"]

    downloaded = client.get(f"/image/{img['id']}/download", headers=headers).content
    from DCT import extract_payload
    decoded = cv2.imdecode(np.frombuffer(downloaded, np.uint8), cv2.IMREAD_COLOR)
    assert extract_payload(decoded).endswith("|© khai")


def test_custom_invisible_text(client):
    headers = sign_in(client)
    img = upload(client, headers)
    r = client.post("/watermark", headers=headers, json={
        "imageId": img["id"], "settings": {"type": "invisible", "text": "Owned by Khai ✓"},
    })
    assert r.json()["invisibleWatermark"] == "Owned by Khai ✓"


# --- sharing ---------------------------------------------------------------

def test_share_link_works_until_revoked(client):
    headers = sign_in(client)
    img = upload(client, headers)
    link = client.post(f"/image/{img['id']}/share", headers=headers).json()["shareUrl"]
    path = link.replace("http://testserver", "")

    assert client.get(path).status_code == 200
    assert client.get("/images", headers=headers).json()["images"][0]["shared"] is True

    r = client.post(f"/image/{img['id']}/revoke", headers=headers)
    assert r.json()["linksRevoked"] == 1
    assert client.get(path).status_code == 404


def test_delete_removes_file_and_share_links(client):
    headers = sign_in(client)
    img = upload(client, headers)
    link = client.post(f"/image/{img['id']}/share", headers=headers).json()["shareUrl"]
    assert client.delete(f"/image/{img['id']}", headers=headers).status_code == 200
    assert client.get(link.replace("http://testserver", "")).status_code == 404
    assert client.get("/images", headers=headers).json()["images"] == []


# --- proof + verify --------------------------------------------------------

def test_proof_of_creation(client):
    headers = sign_in(client)
    img = upload(client, headers)
    proof = client.get(f"/image/{img['id']}/proof", headers=headers).json()
    assert proof["hash"] == img["hashFingerprint"]
    assert proof["owner"] == "khai"


def test_verify_exact_copy_and_recompressed_copy(client):
    headers = sign_in(client)
    img = upload(client, headers)
    original = client.get(f"/image/{img['id']}/download", headers=headers).content

    exact = client.post("/verify", files={"file": ("copy.png", original, "image/png")}).json()
    assert exact["exactMatch"]["owner"] == "khai"
    assert exact["watermark"]["registeredOwner"] == "khai"

    # Someone re-saves it as a JPEG: the hash no longer matches, but the hidden mark survives.
    decoded = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR)
    ok, jpeg = cv2.imencode(".jpg", decoded, [cv2.IMWRITE_JPEG_QUALITY, 85])
    stolen = client.post("/verify", files={"file": ("stolen.jpg", jpeg.tobytes(), "image/jpeg")}).json()
    assert stolen["exactMatch"] is None
    assert stolen["watermark"]["text"] == "© khai"
    assert stolen["watermark"]["registeredOwner"] == "khai"
    assert stolen["protected"] is True


def test_verify_unprotected_image(client):
    r = client.post("/verify", files={"file": ("random.jpg", photo_bytes(), "image/jpeg")}).json()
    assert r["protected"] is False
    assert r["watermark"] is None
