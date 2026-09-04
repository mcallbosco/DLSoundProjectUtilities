from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from historical_content.credentials import (
    delete_saved_api_key,
    load_saved_api_key,
    save_api_key,
)


from historical_content.publishing.credentials import (
    CredentialStoreError,
    delete_credentials,
    load_credentials,
    save_credentials,
)


class PublisherCredentialTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI test")
    def test_dpapi_credential_round_trip_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.dpapi"
            credentials = {
                "r2_access_key_id": "example-access-key",
                "r2_secret_access_key": "example-secret-key",
                "cloudflare_api_token": "example-api-token",
            }
            save_credentials(path, credentials)
            self.assertNotIn(b"example-secret-key", path.read_bytes())
            self.assertEqual(load_credentials(path), credentials)
            delete_credentials(path)
            self.assertFalse(path.exists())
            self.assertEqual(load_credentials(path), {})

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI test")
    def test_corrupt_saved_credentials_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.dpapi"
            path.write_bytes(b"not a DPAPI payload")
            with self.assertRaises(CredentialStoreError):
                load_credentials(path)


@unittest.skipUnless(sys.platform == "win32", "Windows DPAPI test")
class OpenAICredentialTests(unittest.TestCase):
    def test_encrypted_key_survives_copy_to_a_new_path_and_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.dpapi"
            migrated = root / "migrated.dpapi"
            key = "example-openai-key"

            save_api_key(original, key)
            self.assertNotIn(key.encode(), original.read_bytes())
            shutil.copyfile(original, migrated)
            self.assertEqual(load_saved_api_key(migrated), key)

            delete_saved_api_key(migrated)
            self.assertIsNone(load_saved_api_key(migrated))
            self.assertEqual(load_saved_api_key(original), key)

    def test_corrupt_encrypted_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.dpapi"
            path.write_bytes(b"not a DPAPI payload")
            with self.assertRaises(CredentialStoreError):
                load_saved_api_key(path)


if __name__ == "__main__":
    unittest.main()
