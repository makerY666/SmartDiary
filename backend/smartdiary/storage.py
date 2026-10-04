import hashlib
import os
import tempfile

from cryptography.fernet import Fernet

from .config import settings


def encryption():
    key = settings.encryption_key
    if not key:
        keyfile = settings.data_dir / "development.key"
        if not keyfile.exists():
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=settings.data_dir, prefix=".key-")
            try:
                with os.fdopen(handle, "wb") as file:
                    file.write(Fernet.generate_key())
                    file.flush()
                    os.fsync(file.fileno())
                os.chmod(temporary, 0o600)
                try:
                    os.link(temporary, keyfile)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        key = keyfile.read_text().strip()
    return Fernet(key.encode())


def bucket():
    import oss2

    return oss2.Bucket(
        oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret),
        settings.oss_endpoint,
        settings.oss_bucket,
    )


def put(user_id, attachment_id, data):
    key = f"attachments/{user_id}/{attachment_id}.enc"
    encrypted = encryption().encrypt(data)
    if settings.storage_backend == "oss":
        bucket().put_object(key, encrypted, headers={"x-oss-server-side-encryption": "AES256"})
    else:
        path = settings.data_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".media-")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return key, hashlib.sha256(data).hexdigest()


def read(key):
    if settings.storage_backend == "oss":
        encrypted = bucket().get_object(key).read()
    else:
        encrypted = (settings.data_dir / key).read_bytes()
    return encryption().decrypt(encrypted)


def remove(key):
    if settings.storage_backend == "oss":
        bucket().delete_object(key)
    else:
        (settings.data_dir / key).unlink(missing_ok=True)


def valid_media(data, mime):
    if mime.startswith("image/"):
        from io import BytesIO

        from PIL import Image

        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
                return image.format in {"JPEG", "PNG", "WEBP"}
        except Exception:
            return False
    return (
        (mime in {"audio/mp4", "audio/m4a", "audio/aac"} and b"ftyp" in data[:32])
        or (mime in {"audio/wav", "audio/x-wav"} and data[:4] == b"RIFF")
        or (mime == "audio/mpeg" and (data[:3] == b"ID3" or data[:1] == b"\xff"))
    )


def mark_deleted(user_id, record_id):
    key = f"deletion-ledger/{user_id}/{record_id}"
    if settings.storage_backend == "oss":
        bucket().put_object(key, b"deleted")
    else:
        path = settings.data_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as file:
            file.write(b"deleted")
            file.flush()
            os.fsync(file.fileno())


def was_deleted(user_id, record_id):
    key = f"deletion-ledger/{user_id}/{record_id}"
    return (
        bucket().object_exists(key)
        if settings.storage_backend == "oss"
        else (settings.data_dir / key).exists()
    )
