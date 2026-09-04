from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from historical_content.publishing.credentials import (
    CredentialStoreError,
    delete_credentials,
    load_credentials,
    save_credentials,
)


class UtilitySupportTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

