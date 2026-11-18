import unittest
import urllib.request

from pestsentry.fake_cameras import serve


class FakeCameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(0, "127.0.0.1")
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_snapshot_is_jpeg(self):
        url = f"http://127.0.0.1:{self.port}/snapshot/node-3"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type")
        self.assertEqual(ctype, "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertIn(b"\xff\xd9", data)

    def test_mjpeg_stream_starts(self):
        url = f"http://127.0.0.1:{self.port}/stream/node-1"
        with urllib.request.urlopen(url, timeout=5) as resp:
            ctype = resp.headers.get("Content-Type")
            chunk = resp.read(8000)
        self.assertIn("multipart/x-mixed-replace", ctype)
        self.assertIn(b"--frame", chunk)
        self.assertIn(b"\xff\xd8", chunk)

    def test_index_lists_five_nodes(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            body = resp.read().decode()
        for i in range(1, 6):
            self.assertIn(f"/stream/node-{i}", body)


if __name__ == "__main__":
    unittest.main()
