# PhotoGuard 🛡️

Protect your photos before you share them. Built at **SJ Hacks 2026**.

When you upload a photo, PhotoGuard:

1. **Strips private metadata** — GPS location, camera model, serial number and capture date are removed (phone photos often carry your exact location).
2. **Hides an ownership mark inside the image** using the Discrete Cosine Transform (DCT). The photo looks unchanged, but the mark survives re-saving and JPEG compression.
3. **Fingerprints the file** with SHA-256 as proof of when you registered it.

You can then add a visible watermark, share the photo through a link you can revoke at any time, and **verify** any copy you find online to see whether it carries your mark.

## Run it locally

You need **Python 3.10+** and **Node.js 18+**.

### Quick start (macOS / Linux)

```bash
./start.sh
```

Then open **http://localhost:5173**, create an account, and upload a photo.

### Manual start (two terminals, works on Windows too)

**Terminal 1 — backend (port 3000)**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api:app --port 3000 --reload
```

**Terminal 2 — frontend (port 5173)**

```bash
cd frontend
npm install
npm run dev
```

Interactive API docs are at http://localhost:3000/docs.

All data (accounts, images, database) lives in `backend/data/`. Delete that folder to start fresh.

## Try the verify feature

1. Upload a photo and download it from the vault.
2. Re-save it as a JPEG, or crop it lightly in any editor.
3. Open **Verify Ownership** and drop the copy in. PhotoGuard reads the hidden mark and tells you who registered it and when — even though the file's hash no longer matches.

You can also check an image from the command line:

```bash
cd backend && python DCT.py path/to/image.jpg
```

## How it works

```
frontend/  React + TypeScript + Tailwind (Vite dev server, proxies API calls)
backend/
  api.py     FastAPI app: auth, vault, watermarking, sharing, verification
  DCT.py     invisible watermark: embed/extract bits in 8×8 DCT blocks
  tests/     pytest suite (functional + security tests)
  data/      created on first run: SQLite DB, images, server secret
```

### Invisible watermark

Each image is converted to YCrCb and the brightness channel is split into 8×8 blocks. For each block, two mid-frequency DCT coefficients are compared: making one larger than the other encodes a 1 or a 0. Mid frequencies are a good trade-off — changes there are hard to see, but survive JPEG compression, which mostly discards high frequencies.

The hidden message starts with a short header (`PG` + length), so the verifier can tell whether an image is marked at all and read it without knowing the length in advance. The payload also includes part of the image ID, which links a modified copy back to the original record.

### Security

| Area | What PhotoGuard does |
|---|---|
| Passwords | Hashed with salted **scrypt**; never stored or sent back in plain text. Constant-time comparison. |
| Brute force | 5 failed sign-ins locks that username for 5 minutes. |
| Sessions | Random 256-bit bearer tokens, stored only as SHA-256 hashes, expire after 7 days, invalidated on logout. |
| Access control | Every vault request checks the token and the image owner. Other users' images return 404, the same as missing ones. |
| File access | Images are not public. The browser loads them through HMAC-signed URLs. |
| Sharing | Each share is a separate random link; **revoke deletes it** so it stops working immediately. |
| Uploads | 15 MB / 40 MP limits, must decode as a real JPEG, PNG or WebP, then re-encoded server-side (drops EXIF and anything hidden in the file). |
| Paths | File names are server-generated UUIDs, validated on every request — no path traversal. |
| Headers | CORS limited to the frontend origin; `nosniff` and `no-referrer` on every response. |

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

The suite covers the full flow plus attack cases: wrong passwords, lockout, stolen or missing tokens, one user accessing another's files, path traversal, forged file links, HTML uploaded as an "image", revoked share links, and verifying a recompressed copy.

## Limitations / next steps

- The invisible mark survives JPEG compression and re-saving, but not heavy cropping, rotation or resizing. A sync pattern or spread-spectrum embedding would make it more robust.
- The SHA-256 proof is only as trustworthy as this server. Anchoring the hash to an external timestamping service (RFC 3161) would make it independently verifiable.
- Built for local use: for a real deployment, serve over HTTPS and move storage to a managed database and object store.

## Team

Built at SJ Hacks 2026 by Khai Nguyen, Gavin and Duc.

UI components from [shadcn/ui](https://ui.shadcn.com/) (MIT) — see `ATTRIBUTIONS.md`.
