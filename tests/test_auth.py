"""Unit tests for auth.py (PR 5): hash, check, lockout, remove. Uses a temporary auth.json."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = auth.PATH, auth.SECRET_PATH
        auth.PATH = os.path.join(self.dir, "auth.json")
        auth.SECRET_PATH = os.path.join(self.dir, ".storage_secret")
        auth._fails.clear()

    def tearDown(self):
        auth.PATH, auth.SECRET_PATH = self.old

    def test_off_without_a_password(self):
        self.assertFalse(auth.enabled())
        self.assertEqual(auth.check("anything", "a")[0], True)

    def test_set_and_check(self):
        e1 = auth.set_password("correct-horse-1")
        self.assertTrue(auth.enabled())
        self.assertTrue(auth.check("correct-horse-1", "a")[0])
        self.assertFalse(auth.check("wrong", "a")[0])
        self.assertNotIn("correct-horse-1", open(auth.PATH).read())       # only the hash is stored
        e2 = auth.set_password("another-pass-2")
        self.assertNotEqual(e1, e2)                                         # a new password logs browsers out
        self.assertFalse(auth.check("correct-horse-1", "b")[0])

    def test_short_password_refused(self):
        with self.assertRaises(ValueError):
            auth.set_password("short")

    def test_lockout_per_address(self):
        auth.set_password("correct-horse-1")
        for _ in range(auth.MAX_TRIES):
            ok, msg = auth.check("x", "1.1.1.1")
        self.assertIn("Blocked", msg)
        self.assertFalse(auth.check("correct-horse-1", "1.1.1.1")[0])     # even the right one, while blocked
        self.assertTrue(auth.check("correct-horse-1", "2.2.2.2")[0])

    def test_remove(self):
        auth.set_password("correct-horse-1")
        auth.remove_password()
        self.assertFalse(auth.enabled())

    def test_storage_secret_is_kept(self):
        a = auth.storage_secret()
        self.assertEqual(a, auth.storage_secret())
        self.assertGreaterEqual(len(a), 32)


if __name__ == "__main__":
    unittest.main()
