"""
Windows DPAPI encrypt/decrypt for protecting config secrets.
Data is encrypted with the current user account — only this user on this machine can decrypt.
"""
import ctypes
from ctypes import wintypes
import base64


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte))
    ]


_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32


def _free_blob(blob):
    if blob.pbData:
        _kernel32.LocalFree(blob.pbData)


def encrypt(plaintext: str) -> str:
    """Encrypt a string with DPAPI. Returns base64-encoded ciphertext."""
    data = plaintext.encode("utf-8")
    blob_in = _DATA_BLOB(len(data), ctypes.cast(
        ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    if not _crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError(f"CryptProtectData failed: {ctypes.get_last_error()}")
    try:
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        _free_blob(blob_out)


def decrypt(encoded: str) -> str:
    """Decrypt a base64-encoded DPAPI ciphertext back to string."""
    raw = base64.b64decode(encoded)
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(
        ctypes.create_string_buffer(raw, len(raw)), ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError(f"CryptUnprotectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        _free_blob(blob_out)
