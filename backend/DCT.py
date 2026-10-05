import cv2
import numpy as np
from scipy.fftpack import dct, idct

# -----------------------------
# DCT helper functions
# -----------------------------

def dct2(block):
    """Apply 2D DCT to an 8x8 block."""
    return dct(dct(block.T, norm="ortho").T, norm="ortho")


def idct2(block):
    """Apply inverse 2D DCT to an 8x8 block."""
    return idct(idct(block.T, norm="ortho").T, norm="ortho")


# -----------------------------
# Text <-> binary
# -----------------------------

def text_to_bits(text):
    """Convert text into binary bits."""
    bits = []
    for char in text:
        binary = format(ord(char), "08b")
        bits.extend([int(bit) for bit in binary])
    return bits


def bits_to_text(bits):
    """Convert binary bits back into text."""
    chars = []

    for i in range(0, len(bits), 8):
        byte = bits[i:i + 8]

        if len(byte) < 8:
            break

        char_code = int("".join(map(str, byte)), 2)
        chars.append(chr(char_code))

    return "".join(chars)


# -----------------------------
# Embed watermark
# -----------------------------

def embed_watermark(input_path, output_path, watermark_text):
    """
    Embed invisible watermark into an image using DCT.
    """

    img = cv2.imread(input_path)

    if img is None:
        raise ValueError("Image not found or invalid path.")

    # Convert image from BGR to YCrCb
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)

    # Use Y channel because it stores brightness
    y_channel = ycrcb[:, :, 0].astype(np.float32)

    watermark_bits = text_to_bits(watermark_text)

    height, width = y_channel.shape

    bit_index = 0
    total_bits = len(watermark_bits)

    strength = 30  # bigger = stronger watermark, but more distortion

    for row in range(0, height - 8, 8):
        for col in range(0, width - 8, 8):

            if bit_index >= total_bits:
                break

            block = y_channel[row:row + 8, col:col + 8]

            dct_block = dct2(block)

            # Pick two mid-frequency coefficients
            x1, y1 = 3, 4
            x2, y2 = 4, 3

            bit = watermark_bits[bit_index]

            c1 = dct_block[x1, y1]
            c2 = dct_block[x2, y2]

            # Embed bit using coefficient comparison
            if bit == 1:
                dct_block[x1, y1] = max(c1, c2) + strength
                dct_block[x2, y2] = min(c1, c2)
            else:
                dct_block[x2, y2] = max(c1, c2) + strength
                dct_block[x1, y1] = min(c1, c2)
            watermarked_block = idct2(dct_block)

            y_channel[row:row + 8, col:col + 8] = watermarked_block

            bit_index += 1

        if bit_index >= total_bits:
            break

    if bit_index < total_bits:
        raise ValueError("Image is too small to store this watermark.")

    # Clip values back into valid image range
    y_channel = np.clip(y_channel, 0, 255).astype(np.uint8)

    # Put modified Y channel back
    ycrcb[:, :, 0] = y_channel

    # Convert back to BGR
    result = cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

    cv2.imwrite(output_path, result)

    print("Watermark embedded successfully.")
    print("Output saved to:", output_path)


# -----------------------------
# Extract watermark
# -----------------------------

def extract_watermark(input_path, watermark_length):
    """
    Extract invisible watermark from image.
    watermark_length = number of characters expected.
    """

    img = cv2.imread(input_path)

    if img is None:
        raise ValueError("Image not found or invalid path.")

    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    y_channel = ycrcb[:, :, 0].astype(np.float32)

    height, width = y_channel.shape

    total_bits = watermark_length * 8
    extracted_bits = []

    for row in range(0, height - 8, 8):
        for col in range(0, width - 8, 8):

            if len(extracted_bits) >= total_bits:
                break

            block = y_channel[row:row + 8, col:col + 8]

            dct_block = dct2(block)

            x1, y1 = 3, 4
            x2, y2 = 4, 3

            c1 = dct_block[x1, y1]
            c2 = dct_block[x2, y2]

            bit = 1 if c1 > c2 else 0
            extracted_bits.append(bit)

        if len(extracted_bits) >= total_bits:
            break

    watermark_text = bits_to_text(extracted_bits)

    return watermark_text


# -----------------------------
# Self-describing payloads (used by the API)
# -----------------------------
#
# The functions above need the caller to know the watermark length ahead of
# time, which makes it impossible to verify an unknown image. The functions
# below write a small header first:
#
#     b"PG" (magic) + 1 byte payload length + UTF-8 payload bytes
#
# so `extract_payload` can tell whether an image carries a PhotoGuard mark at
# all, and read it back without any extra information. UTF-8 also fixes
# characters outside Latin-1 (emoji, accents), which `text_to_bits` mangles.

MAGIC = b"PG"
MAX_PAYLOAD_BYTES = 255
_C1 = (3, 4)
_C2 = (4, 3)
_STRENGTH = 30


def _bytes_to_bits(data: bytes):
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def _bits_to_bytes(bits) -> bytes:
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def _block_positions(height, width):
    for row in range(0, height - 8, 8):
        for col in range(0, width - 8, 8):
            yield row, col


def capacity_bytes(img) -> int:
    """How many payload bytes an image can hold (after the 3-byte header)."""
    h, w = img.shape[:2]
    blocks = len(range(0, h - 8, 8)) * len(range(0, w - 8, 8))
    return max(0, blocks // 8 - len(MAGIC) - 1)


def embed_payload(img, payload: str):
    """Return a copy of a BGR image with `payload` hidden in its DCT coefficients."""
    data = payload.encode("utf-8")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ValueError("Watermark text is too long.")
    if len(data) > capacity_bytes(img):
        raise ValueError("Image is too small to store this watermark.")

    bits = _bytes_to_bits(MAGIC + bytes([len(data)]) + data)
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    y_channel = ycrcb[:, :, 0].astype(np.float32)

    for bit, (row, col) in zip(bits, _block_positions(*y_channel.shape)):
        block = dct2(y_channel[row:row + 8, col:col + 8])
        c1, c2 = block[_C1], block[_C2]
        hi, lo = max(c1, c2) + _STRENGTH, min(c1, c2)
        block[_C1], block[_C2] = (hi, lo) if bit else (lo, hi)
        y_channel[row:row + 8, col:col + 8] = idct2(block)

    ycrcb[:, :, 0] = np.clip(y_channel, 0, 255).astype(np.uint8)
    return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)


def extract_payload(img):
    """Return the hidden payload string, or None if the image has no PhotoGuard mark."""
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    y_channel = ycrcb[:, :, 0].astype(np.float32)
    positions = _block_positions(*y_channel.shape)

    def read_bytes(count):
        bits = []
        for row, col in positions:
            block = dct2(y_channel[row:row + 8, col:col + 8])
            bits.append(1 if block[_C1] > block[_C2] else 0)
            if len(bits) == count * 8:
                break
        return _bits_to_bytes(bits) if len(bits) == count * 8 else None

    header = read_bytes(len(MAGIC) + 1)
    if not header or header[:len(MAGIC)] != MAGIC:
        return None
    body = read_bytes(header[-1])
    if body is None:
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


# -----------------------------
# Example usage
# -----------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python DCT.py <image>  — prints the hidden PhotoGuard mark, if any")
        sys.exit(1)

    image = cv2.imread(sys.argv[1])
    if image is None:
        sys.exit("Could not read that image.")
    print("Hidden mark:", extract_payload(image))