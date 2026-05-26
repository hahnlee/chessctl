from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from chessctl.cli import main


class ChessCtlCliTest(unittest.TestCase):
    def invoke(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def run_cli(self, *args: str) -> int:
        code, _stdout, _stderr = self.invoke(*args)
        return code

    def read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_new_and_apply_store_uci_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game.json"

            self.assertEqual(self.run_cli("new", "--game", str(game)), 0)
            self.assertEqual(self.run_cli("apply", "--game", str(game), "--move", "e2e4"), 0)

            data = self.read_json(game)
            self.assertEqual(data["moves"], ["e2e4"])

    def test_illegal_move_is_rejected_and_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game.json"

            self.assertEqual(self.run_cli("new", "--game", str(game)), 0)
            self.assertEqual(self.run_cli("inspect", "--game", str(game), "--move", "e2e5"), 2)
            self.assertEqual(self.run_cli("apply", "--game", str(game), "--move", "e2e5"), 2)

            data = self.read_json(game)
            self.assertEqual(data["moves"], [])

    def test_capture_detail_for_normal_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game.json"

            self.assertEqual(self.run_cli("new", "--game", str(game)), 0)
            for move in ["e2e4", "d7d5"]:
                self.assertEqual(self.run_cli("apply", "--game", str(game), "--move", move), 0)

            # e4xd5 captures the black pawn on d5. This validates the game can
            # distinguish a legal capture from an ordinary legal move.
            code, stdout, _stderr = self.invoke("inspect", "--game", str(game), "--move", "e4d5")
            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertTrue(payload["legal"])
            self.assertEqual(payload["detail"]["capture"]["captured_piece"]["type"], "pawn")
            self.assertEqual(payload["detail"]["capture"]["captured_piece"]["color"], "black")
            self.assertEqual(payload["detail"]["capture"]["captured_square"], "d5")

    def test_checkmate_outcome_after_fools_mate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game.json"

            self.assertEqual(self.run_cli("new", "--game", str(game)), 0)
            for move in ["f2f3", "e7e5", "g2g4", "d8h4"]:
                self.assertEqual(self.run_cli("apply", "--game", str(game), "--move", move), 0)

            data = self.read_json(game)
            self.assertEqual(data["moves"], ["f2f3", "e7e5", "g2g4", "d8h4"])

            code, stdout, _stderr = self.invoke("outcome", "--game", str(game))
            self.assertEqual(code, 0)
            outcome = json.loads(stdout)["outcome"]
            self.assertTrue(outcome["is_game_over"])
            self.assertTrue(outcome["is_checkmate"])
            self.assertEqual(outcome["result"], "0-1")
            self.assertEqual(outcome["winner"], "black")


if __name__ == "__main__":
    unittest.main()
