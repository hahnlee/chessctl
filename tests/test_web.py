from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import chess

from chessctl.web import create_server


class WebServerTest(unittest.TestCase):
    def request(
        self,
        host: str,
        port: int,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else {}
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = json.loads(response.read().decode("utf-8"))
            return response.status, data
        finally:
            connection.close()

    def test_user_move_waits_for_manual_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game.json"
            server = create_server("127.0.0.1", 0, game_path=game, engine_color=chess.BLACK)
            host, port = server.server_address
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, data = self.request(host, port, "POST", "/api/new", {"human_color": "white"})
                self.assertEqual(status, 200)
                self.assertEqual(data["human_color"], "white")
                self.assertEqual(data["engine_color"], "black")

                status, data = self.request(host, port, "POST", "/api/move", {"move": "e2e4"})
                self.assertEqual(status, 200)
                self.assertEqual(len(data["applied_moves"]), 1)
                self.assertEqual(data["applied_moves"][0]["uci"], "e2e4")
                self.assertEqual(data["state"]["turn"], "black")
                self.assertTrue(data["is_engine_turn"])
                self.assertEqual(len(data["state"]["game"]["moves"]), 1)

                status, data = self.request(host, port, "POST", "/api/engine", {})
                self.assertEqual(status, 404)
                self.assertEqual(data["error"], "not found")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_new_as_black_waits_for_manual_engine_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game.json"
            server = create_server("127.0.0.1", 0, game_path=game, engine_color=chess.BLACK)
            host, port = server.server_address
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, data = self.request(host, port, "POST", "/api/new", {"human_color": "black"})
                self.assertEqual(status, 200)
                self.assertEqual(data["human_color"], "black")
                self.assertEqual(data["engine_color"], "white")
                self.assertEqual(len(data["applied_moves"]), 0)
                self.assertEqual(data["state"]["turn"], "white")
                self.assertTrue(data["is_engine_turn"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
