from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import chess

from .cli import (
    STARTING_FEN,
    ChessCtlError,
    GameState,
    board_from_state,
    color_name,
    load_game,
    move_payload,
    new_game_state,
    outcome_payload,
    parse_move,
    save_game,
    state_payload,
    utc_now,
)


def color_from_name(name: str) -> chess.Color:
    normalized = name.strip().lower()
    if normalized == "white":
        return chess.WHITE
    if normalized == "black":
        return chess.BLACK
    raise ChessCtlError("color must be white or black")


def opposite_color(color: chess.Color) -> chess.Color:
    return chess.BLACK if color == chess.WHITE else chess.WHITE


def state_engine_color(state: GameState, fallback: chess.Color) -> chess.Color:
    raw = state.metadata.get("engine_color")
    if isinstance(raw, str):
        try:
            return color_from_name(raw)
        except ChessCtlError:
            return fallback
    return fallback


def state_human_color(state: GameState, fallback_engine: chess.Color) -> chess.Color:
    raw = state.metadata.get("human_color")
    if isinstance(raw, str):
        try:
            return color_from_name(raw)
        except ChessCtlError:
            return opposite_color(fallback_engine)
    return opposite_color(fallback_engine)


def create_session_state(path: Path, *, human_color: chess.Color, fen: str = STARTING_FEN) -> GameState:
    engine_color = opposite_color(human_color)
    state = new_game_state(path, fen)
    state = GameState(
        path=state.path,
        initial_fen=state.initial_fen,
        moves=state.moves,
        metadata={
            **state.metadata,
            "human_color": color_name(human_color),
            "engine_color": color_name(engine_color),
        },
    )
    save_game(state)
    return load_game(path)


def ensure_game(path: Path, *, engine_color: chess.Color) -> GameState:
    if path.exists():
        return load_game(path)
    return create_session_state(path, human_color=opposite_color(engine_color))


def legal_moves_payload(board: chess.Board) -> list[dict[str, Any]]:
    return [move_payload(board, move) for move in board.legal_moves]


def session_payload(state: GameState, *, fallback_engine_color: chess.Color) -> dict[str, Any]:
    board = board_from_state(state)
    engine_color = state_engine_color(state, fallback_engine_color)
    human_color = state_human_color(state, engine_color)
    outcome = outcome_payload(board)
    return {
        "state": state_payload(board, state),
        "legal_moves": legal_moves_payload(board),
        "human_color": color_name(human_color),
        "engine_color": color_name(engine_color),
        "is_engine_turn": board.turn == engine_color and not outcome["is_game_over"],
        "outcome": outcome,
    }


def apply_move_to_state(state: GameState, raw_move: str) -> tuple[GameState, dict[str, Any]]:
    board = board_from_state(state)
    move = parse_move(board, raw_move)
    if move not in board.legal_moves:
        raise ChessCtlError(
            "illegal move",
            payload={"move": raw_move, "legal_moves": legal_moves_payload(board)},
        )

    detail = move_payload(board, move)
    next_state = GameState(
        path=state.path,
        initial_fen=state.initial_fen,
        moves=[*state.moves, move.uci()],
        metadata={**state.metadata, "updated_at": utc_now()},
    )
    save_game(next_state)
    return load_game(next_state.path or Path("game.json")), detail


def static_bytes(path: str) -> tuple[bytes, str]:
    safe_path = path.strip("/") or "index.html"
    if safe_path == "":
        safe_path = "index.html"
    if ".." in Path(safe_path).parts:
        raise FileNotFoundError(safe_path)

    package_files = resources.files("chessctl.web_static")
    target = package_files.joinpath(safe_path)
    if not target.is_file():
        raise FileNotFoundError(safe_path)
    content_type = mimetypes.guess_type(safe_path)[0] or "application/octet-stream"
    return target.read_bytes(), content_type


class ChessWebServer(ThreadingHTTPServer):
    game_path: Path
    fallback_engine_color: chess.Color


class ChessWebHandler(BaseHTTPRequestHandler):
    server: ChessWebServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: HTTPStatus, payload: dict[str, Any] | None = None) -> None:
        self.send_json({"error": message, **(payload or {})}, status=status)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ChessCtlError(f"invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ChessCtlError("JSON body must be an object")
        return payload

    def current_state(self) -> GameState:
        return ensure_game(self.server.game_path, engine_color=self.server.fallback_engine_color)

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/state":
            state = self.current_state()
            self.send_json(session_payload(state, fallback_engine_color=self.server.fallback_engine_color))
            return

        safe_route = "index.html" if route == "/" else unquote(route.lstrip("/"))
        try:
            body, content_type = static_bytes(safe_route)
        except FileNotFoundError:
            self.send_error_json("not found", HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self.read_json_body()
            if route == "/api/new":
                human_color = color_from_name(str(payload.get("human_color", "white")))
                fen = str(payload.get("fen", STARTING_FEN))
                state = create_session_state(self.server.game_path, human_color=human_color, fen=fen)
                self.send_json(
                    {
                        **session_payload(state, fallback_engine_color=self.server.fallback_engine_color),
                        "applied_moves": [],
                    }
                )
                return

            if route == "/api/move":
                state = self.current_state()
                board = board_from_state(state)
                engine_color = state_engine_color(state, self.server.fallback_engine_color)
                outcome = outcome_payload(board)
                if outcome["is_game_over"]:
                    self.send_error_json("game is over", HTTPStatus.CONFLICT, {"outcome": outcome})
                    return
                if board.turn == engine_color:
                    self.send_error_json("it is the engine turn", HTTPStatus.CONFLICT)
                    return

                raw_move = str(payload.get("move", "")).strip()
                if not raw_move:
                    raise ChessCtlError("move is required")

                state, user_move = apply_move_to_state(state, raw_move)
                self.send_json(
                    {
                        **session_payload(state, fallback_engine_color=self.server.fallback_engine_color),
                        "applied_moves": [user_move],
                    }
                )
                return

            self.send_error_json("not found", HTTPStatus.NOT_FOUND)
        except ChessCtlError as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST, exc.payload)


def create_server(host: str, port: int, *, game_path: Path, engine_color: chess.Color) -> ChessWebServer:
    ensure_game(game_path, engine_color=engine_color)
    server = ChessWebServer((host, port), ChessWebHandler)
    server.game_path = game_path
    server.fallback_engine_color = engine_color
    return server


def run_web_server(args: argparse.Namespace) -> int:
    game_path = Path(args.game).expanduser()
    engine_color = color_from_name(args.engine_color)
    server = create_server(args.host, args.port, game_path=game_path, engine_color=engine_color)
    host, port = server.server_address
    print(f"chessctl web serving http://{host}:{port} using {game_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
