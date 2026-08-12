"""
Encryption Service for API Credential Management
Mengenkripsi API Key dan Secret Key sebelum disimpan di database
"""
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import padding

from config.settings import ENCRYPTION_KEY


class EncryptionService:
    """Service untuk mengenkripsi/mendekripsi API credentials"""

    def __init__(self, key: Optional[str] = None):
        self.key = key or ENCRYPTION_KEY
        if not self.key:
            raise ValueError(
                "ENCRYPTION_KEY tidak ditemukan. "
                "Set ENCRYPTION_KEY di .env file atau environment variables.\n"
                "Generate key: node -e \"console.log(require('crypto').randomBytes(32).toString('hex'))\""
            )
        self.key_bytes = self.key.encode() if isinstance(self.key, str) else self.key
        if len(self.key_bytes) < 16:
            raise ValueError(
                "ENCRYPTION_KEY minimal 16 byte; gunakan secret acak 32 byte atau lebih."
            )

    @staticmethod
    def _derive_key(master_key: bytes, salt: bytes) -> bytes:
        return hashlib.scrypt(
            master_key, salt=salt, n=16384, r=8, p=1, dklen=32,
            maxmem=64 * 1024 * 1024,
        )

    @staticmethod
    def _aad(version: str, context: str = '') -> bytes:
        suffix = f":{context}" if context else ''
        return f"xbot-credential-{version}{suffix}".encode()

    def encrypt(self, plaintext: str, context: str = '') -> str:
        """Encrypt plaintext string"""
        if not plaintext:
            return ""
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = self._derive_key(self.key_bytes, salt)
        version = 'v3' if context else 'v2'
        encrypted_and_tag = AESGCM(key).encrypt(
            nonce, plaintext.encode(), self._aad(version, context))
        ciphertext, tag = encrypted_and_tag[:-16], encrypted_and_tag[-16:]
        return f"{version}:{salt.hex()}:{nonce.hex()}:{tag.hex()}:{ciphertext.hex()}"

    def decrypt(self, ciphertext: str, context: str = '') -> str:
        """Decrypt ciphertext string"""
        if not ciphertext:
            return ""
        if ciphertext.startswith(('v2:', 'v3:')):
            try:
                version, salt_hex, nonce_hex, tag_hex, encrypted_hex = ciphertext.split(':')
                if version not in ('v2', 'v3'):
                    raise ValueError('Unsupported credential version')
                if version == 'v3' and not context:
                    raise ValueError('Account context is required for v3 credential')
                salt = bytes.fromhex(salt_hex)
                nonce = bytes.fromhex(nonce_hex)
                tag = bytes.fromhex(tag_hex)
                encrypted = bytes.fromhex(encrypted_hex)
                if len(salt) != 16 or len(nonce) != 12 or len(tag) != 16:
                    raise ValueError('Invalid v2 credential parameters')
                key = self._derive_key(self.key_bytes, salt)
                return AESGCM(key).decrypt(
                    nonce, encrypted + tag,
                    self._aad(version, context) if version == 'v3'
                    else b"xbot-credential-v2"
                ).decode()
            except (ValueError, UnicodeDecodeError, InvalidTag) as error:
                raise ValueError("AES-GCM credential payload is invalid") from error
        if ':' in ciphertext:
            try:
                iv_hex, encrypted_hex = ciphertext.split(':', 1)
                iv = bytes.fromhex(iv_hex)
                encrypted = bytes.fromhex(encrypted_hex)
                legacy_key = self._derive_key(self.key_bytes, b"salt")
                decryptor = Cipher(algorithms.AES(legacy_key), modes.CBC(iv)).decryptor()
                padded = decryptor.update(encrypted) + decryptor.finalize()
                unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
                return (unpadder.update(padded) + unpadder.finalize()).decode()
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError("Node AES credential payload is invalid") from error
        try:
            return Fernet(self.key_bytes).decrypt(ciphertext.encode()).decode()
        except Exception as error:
            raise ValueError("Legacy Fernet credential payload is invalid") from error

    @staticmethod
    def mask_credential(credential: str, visible_start: int = 6, visible_end: int = 4) -> str:
        """Mask credential for display (e.g., ABCDEF****1234)"""
        if not credential:
            return ""
        if len(credential) <= visible_start + visible_end:
            return credential[:visible_start] + "****"
        return credential[:visible_start] + "*" * (len(credential) - visible_start - visible_end) + credential[-visible_end:]
