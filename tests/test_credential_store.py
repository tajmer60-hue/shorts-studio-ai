import os
from pathlib import Path
import tempfile
import unittest
from credential_store import load_key,save_key


@unittest.skipUnless(os.name=='nt','Windows DPAPI')
class CredentialStoreTests(unittest.TestCase):
    def test_encrypted_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.dpapi'
            self.assertEqual(load_key(path),'')
            save_key(path,'test-only-not-a-real-key')
            self.assertNotIn(b'test-only-not-a-real-key',path.read_bytes())
            self.assertEqual(load_key(path),'test-only-not-a-real-key')

    def test_invalid_encrypted_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.dpapi';path.write_bytes(b'invalid')
            with self.assertRaises(ValueError):load_key(path)
