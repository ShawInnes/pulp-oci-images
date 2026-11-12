"""Run with the Pulp image's Python, which includes cryptography."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

spec = importlib.util.spec_from_file_location(
    "init_container_auth", Path(__file__).resolve().parents[1] / "assets/init_container_auth.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ContainerAuthKeysTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.private = self.root / "token_private_key.pem"
        self.public = self.root / "token_public_key.pem"

    def test_create_and_reuse_without_rotation(self):
        module.initialize(self.root)
        initial = (self.private.read_bytes(), self.public.read_bytes())
        self.assertIn("Reused", module.initialize(self.root))
        self.assertEqual(initial, (self.private.read_bytes(), self.public.read_bytes()))
        self.assertEqual(stat.S_IMODE(self.private.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.public.stat().st_mode), 0o644)

    def test_derive_missing_public_key(self):
        module.initialize(self.root)
        initial = (self.private.read_bytes(), self.public.read_bytes())
        self.public.unlink()
        self.assertIn("Recovered", module.initialize(self.root))
        self.assertEqual(initial, (self.private.read_bytes(), self.public.read_bytes()))

    def test_public_only_fails_without_replacing_it(self):
        module.initialize(self.root)
        original = self.public.read_bytes()
        self.private.unlink()
        with self.assertRaisesRegex(RuntimeError, "without its private"):
            module.initialize(self.root)
        self.assertEqual(original, self.public.read_bytes())
        self.assertFalse(self.private.exists())

    def test_mismatched_pair_fails_without_rotation(self):
        module.initialize(self.root)
        other = ec.generate_private_key(ec.SECP256R1()).public_key()
        self.public.write_bytes(other.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ))
        original = (self.private.read_bytes(), self.public.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            module.initialize(self.root)
        self.assertEqual(original, (self.private.read_bytes(), self.public.read_bytes()))

    def test_wrong_curve_fails(self):
        private = ec.generate_private_key(ec.SECP384R1())
        self.private.write_bytes(private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        with self.assertRaisesRegex(RuntimeError, "P-256"):
            module.initialize(self.root)
        self.assertFalse(self.public.exists())

    def test_invalid_private_key_fails_without_replacing_it(self):
        self.private.write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            module.initialize(self.root)
        self.assertEqual(self.private.read_bytes(), b"invalid")

    def test_concurrent_initializers_share_one_pair(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: module.initialize(self.root), range(8)))
        self.assertEqual(sum(result.startswith("Created") for result in results), 1)
        self.assertEqual(sum(result.startswith("Reused") for result in results), 7)

    def test_symlink_is_rejected(self):
        target = self.root / "other"
        target.write_bytes(b"preserve")
        self.private.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            module.initialize(self.root)
        self.assertEqual(target.read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
