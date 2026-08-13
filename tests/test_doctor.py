from __future__ import annotations

import socket
import unittest

from research_peer.doctor import classify_socket_error, local_network_checks


class DoctorTests(unittest.TestCase):
    def test_error_classification(self) -> None:
        self.assertEqual("DNS_FAILURE", classify_socket_error(socket.gaierror(-2, "no name")))
        self.assertEqual("CONNECTION_REFUSED", classify_socket_error(ConnectionRefusedError()))
        self.assertEqual("TIMEOUT", classify_socket_error(socket.timeout()))

    def test_local_checks_never_crash(self) -> None:
        results = local_network_checks()
        self.assertTrue(results)
        self.assertIn(results[0]["status"], {"pass", "fail"})


if __name__ == "__main__":
    unittest.main()

