from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chess
import chess.pgn


STARTING_FEN = chess.STARTING_FEN
SCHEMA_VERSION = 1


class ChessCtlError(Exception):
    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


@dataclass(frozen=True)
class GameState:
    path: Path | None
    initial_fen: str
    moves: list[str]
    metadata: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def color_name(color: chess.Color | None) -> str | None:
    if color is None:
        return None
    return "white" if color == chess.WHITE else "black"


def piece_type_name(piece_type: chess.PieceType) -> str:
    return {
        chess.PAWN: "pawn",
        chess.KNIGHT: "knight",
        chess.BISHOP: "bishop",
        chess.ROOK: "rook",
        chess.QUEEN: "queen",
        chess.KING: "king",
    }[piece_type]


def piece_payload(piece: chess.Piece | None, *, square: chess.Square | None = None) -> dict[str, Any] | None:
    if piece is None:
        return None

    payload: dict[str, Any] = {
        "color": color_name(piece.color),
        "type": piece_type_name(piece.piece_type),
        "symbol": piece.symbol(),
    }
    if square is not None:
        payload["square"] = chess.square_name(square)
    return payload


def parse_fen(fen: str) -> str:
    try:
        chess.Board(fen)
    except ValueError as exc:
        raise ChessCtlError(f"invalid FEN: {exc}") from exc
    return fen


def load_game(path: Path) -> GameState:
    if not path.exists():
        raise ChessCtlError(f"game file does not exist: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ChessCtlError(f"invalid game JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ChessCtlError("game file must contain a JSON object")

    initial_fen = raw.get("initial_fen", STARTING_FEN)
    if not isinstance(initial_fen, str):
        raise ChessCtlError("initial_fen must be a string")
    parse_fen(initial_fen)

    moves = raw.get("moves", [])
    if not isinstance(moves, list) or not all(isinstance(move, str) for move in moves):
        raise ChessCtlError("moves must be an array of UCI move strings")

    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ChessCtlError("metadata must be an object")

    return GameState(path=path, initial_fen=initial_fen, moves=list(moves), metadata=metadata)


def new_game_state(path: Path | None, fen: str) -> GameState:
    return GameState(
        path=path,
        initial_fen=parse_fen(fen),
        moves=[],
        metadata={"created_at": utc_now(), "updated_at": utc_now()},
    )


def game_to_json(state: GameState) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "initial_fen": state.initial_fen,
        "moves": state.moves,
        "metadata": state.metadata,
    }


def save_game(state: GameState, path: Path | None = None) -> None:
    target = path or state.path
    if target is None:
        raise ChessCtlError("cannot save a stateless FEN position without --game")

    payload = game_to_json(state)
    payload["metadata"] = {**payload.get("metadata", {}), "updated_at": utc_now()}
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(target)


def board_from_state(state: GameState) -> chess.Board:
    board = chess.Board(state.initial_fen)
    for index, uci in enumerate(state.moves, start=1):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError as exc:
            raise ChessCtlError(f"stored move #{index} is not UCI: {uci}") from exc

        if move not in board.legal_moves:
            raise ChessCtlError(f"stored move #{index} is illegal from reconstructed board: {uci}")
        board.push(move)
    return board


def state_from_args(args: argparse.Namespace) -> GameState:
    game_path = Path(args.game).expanduser() if getattr(args, "game", None) else None
    fen = getattr(args, "fen", None)

    if game_path and fen:
        raise ChessCtlError("use either --game or --fen, not both")
    if game_path:
        return load_game(game_path)
    if fen:
        return new_game_state(None, fen)
    raise ChessCtlError("provide --game or --fen")


def parse_move(board: chess.Board, raw: str) -> chess.Move:
    normalized = raw.strip()
    if not normalized:
        raise ChessCtlError("move must not be empty")

    try:
        move = chess.Move.from_uci(normalized.lower())
    except ValueError:
        try:
            move = board.parse_san(normalized)
        except ValueError as exc:
            raise ChessCtlError(
                f"could not parse move as UCI or SAN: {raw}",
                payload={"move": raw, "legal": False},
            ) from exc

    return move


def outcome_payload(board: chess.Board) -> dict[str, Any]:
    outcome = board.outcome(claim_draw=True)
    status = {
        "is_game_over": board.is_game_over(claim_draw=True),
        "is_check": board.is_check(),
        "is_checkmate": board.is_checkmate(),
        "is_stalemate": board.is_stalemate(),
        "can_claim_draw": board.can_claim_draw(),
        "is_insufficient_material": board.is_insufficient_material(),
        "is_seventyfive_moves": board.is_seventyfive_moves(),
        "is_fivefold_repetition": board.is_fivefold_repetition(),
    }

    if outcome is None:
        return {
            **status,
            "result": "*",
            "termination": None,
            "winner": None,
        }

    return {
        **status,
        "result": outcome.result(),
        "termination": outcome.termination.name.lower(),
        "winner": color_name(outcome.winner),
    }


def board_matrix(board: chess.Board) -> list[list[str | None]]:
    matrix: list[list[str | None]] = []
    for rank in range(7, -1, -1):
        row: list[str | None] = []
        for file_index in range(8):
            piece = board.piece_at(chess.square(file_index, rank))
            row.append(piece.symbol() if piece else None)
        matrix.append(row)
    return matrix


def pieces_payload(board: chess.Board) -> list[dict[str, Any]]:
    pieces: list[dict[str, Any]] = []
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is not None:
            pieces.append(piece_payload(piece, square=square) or {})
    return sorted(pieces, key=lambda item: item["square"])


def state_payload(board: chess.Board, state: GameState | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "fen": board.fen(),
        "turn": color_name(board.turn),
        "fullmove_number": board.fullmove_number,
        "halfmove_clock": board.halfmove_clock,
        "castling_xfen": board.castling_xfen(),
        "ep_square": chess.square_name(board.ep_square) if board.ep_square is not None else None,
        "ascii": str(board),
        "board": board_matrix(board),
        "pieces": pieces_payload(board),
        "legal_move_count": board.legal_moves.count(),
        "outcome": outcome_payload(board),
    }
    if state is not None:
        payload["game"] = {
            "path": str(state.path) if state.path is not None else None,
            "initial_fen": state.initial_fen,
            "moves": state.moves,
            "ply": len(state.moves),
            "pgn": pgn_from_state(state),
        }
    return payload


def pgn_from_state(state: GameState) -> str:
    game = chess.pgn.Game()
    game.setup(chess.Board(state.initial_fen))
    game.headers["Result"] = "*"
    board = chess.Board(state.initial_fen)
    node = game

    for uci in state.moves:
        move = chess.Move.from_uci(uci)
        node = node.add_variation(move)
        board.push(move)

    result = board.result(claim_draw=True)
    game.headers["Result"] = result
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    return game.accept(exporter)


def capture_payload(board: chess.Board, move: chess.Move) -> dict[str, Any]:
    if not board.is_capture(move):
        return {"is_capture": False, "captured_piece": None, "captured_square": None}

    captured_square = move.to_square
    if board.is_en_passant(move):
        captured_square = move.to_square - 8 if board.turn == chess.WHITE else move.to_square + 8

    return {
        "is_capture": True,
        "captured_piece": piece_payload(board.piece_at(captured_square), square=captured_square),
        "captured_square": chess.square_name(captured_square),
        "is_en_passant": board.is_en_passant(move),
    }


def move_payload(board: chess.Board, move: chess.Move) -> dict[str, Any]:
    if move not in board.legal_moves:
        return {
            "uci": move.uci(),
            "legal": False,
            "from": chess.square_name(move.from_square),
            "to": chess.square_name(move.to_square),
        }

    moving_piece = board.piece_at(move.from_square)
    after = board.copy()
    san = board.san(move)
    gives_check = board.gives_check(move)
    capture = capture_payload(board, move)
    after.push(move)

    return {
        "uci": move.uci(),
        "san": san,
        "legal": True,
        "from": chess.square_name(move.from_square),
        "to": chess.square_name(move.to_square),
        "piece": piece_payload(moving_piece, square=move.from_square),
        "promotion": piece_type_name(move.promotion) if move.promotion else None,
        "is_castling": board.is_castling(move),
        "is_kingside_castling": board.is_kingside_castling(move),
        "is_queenside_castling": board.is_queenside_castling(move),
        "gives_check": gives_check,
        "gives_checkmate": after.is_checkmate(),
        "capture": capture,
        "fen_after": after.fen(),
        "outcome_after": outcome_payload(after),
    }


def inspect_payload(board: chess.Board, raw_move: str) -> dict[str, Any]:
    before = state_payload(board)
    try:
        move = parse_move(board, raw_move)
    except ChessCtlError as exc:
        return {
            "move": raw_move,
            "legal": False,
            "error": str(exc),
            "before": before,
        }

    if move not in board.legal_moves:
        return {
            "move": raw_move,
            "uci": move.uci(),
            "legal": False,
            "error": "illegal move",
            "before": before,
            "legal_moves": [move_payload(board, legal_move) for legal_move in board.legal_moves],
        }

    detail = move_payload(board, move)
    return {
        "move": raw_move,
        "legal": True,
        "detail": detail,
        "before": before,
        "after": state_payload(board_after(board, move)),
    }


def board_after(board: chess.Board, move: chess.Move) -> chess.Board:
    after = board.copy()
    after.push(move)
    return after


def output(payload: dict[str, Any] | list[Any], *, compact: bool = False) -> None:
    indent = None if compact else 2
    print(json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=False))


def cmd_new(args: argparse.Namespace) -> int:
    path = Path(args.game).expanduser()
    if path.exists() and not args.force:
        raise ChessCtlError(f"game file already exists: {path}; pass --force to overwrite")

    state = new_game_state(path, args.fen or STARTING_FEN)
    save_game(state, path)
    board = board_from_state(load_game(path))
    output({"created": True, "game": str(path), "state": state_payload(board, state)}, compact=args.compact)
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    state = state_from_args(args)
    board = board_from_state(state)
    output(state_payload(board, state), compact=args.compact)
    return 0


def cmd_legal(args: argparse.Namespace) -> int:
    state = state_from_args(args)
    board = board_from_state(state)
    payload = {
        "fen": board.fen(),
        "turn": color_name(board.turn),
        "legal_move_count": board.legal_moves.count(),
        "legal_moves": [move_payload(board, move) for move in board.legal_moves],
        "outcome": outcome_payload(board),
    }
    output(payload, compact=args.compact)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    state = state_from_args(args)
    board = board_from_state(state)
    payload = inspect_payload(board, args.move)
    output(payload, compact=args.compact)
    return 0 if payload["legal"] else 2


def cmd_apply(args: argparse.Namespace) -> int:
    state = state_from_args(args)
    if state.path is None:
        raise ChessCtlError("apply requires --game; use inspect with --fen for stateless analysis")

    board = board_from_state(state)
    payload = inspect_payload(board, args.move)
    if not payload["legal"]:
        output({"applied": False, **payload}, compact=args.compact)
        return 2

    move = parse_move(board, args.move)
    next_state = GameState(
        path=state.path,
        initial_fen=state.initial_fen,
        moves=[*state.moves, move.uci()],
        metadata={**state.metadata, "updated_at": utc_now()},
    )
    save_game(next_state)
    next_board = board_from_state(next_state)
    output(
        {
            "applied": True,
            "move": move_payload(board, move),
            "state": state_payload(next_board, next_state),
        },
        compact=args.compact,
    )
    return 0


def cmd_outcome(args: argparse.Namespace) -> int:
    state = state_from_args(args)
    board = board_from_state(state)
    output({"fen": board.fen(), "outcome": outcome_payload(board)}, compact=args.compact)
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web import run_web_server

    return run_web_server(args)


def add_position_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--game", help="Path to a chessctl game JSON file.")
    group.add_argument("--fen", help="Stateless FEN position.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chessctl",
        description="Agent-friendly chess rules, move inspection, and game-state CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a new game JSON file.")
    new_parser.add_argument("--game", required=True, help="Path to write the game JSON file.")
    new_parser.add_argument("--fen", default=STARTING_FEN, help="Initial FEN. Defaults to standard chess.")
    new_parser.add_argument("--force", action="store_true", help="Overwrite an existing game file.")
    new_parser.add_argument("--compact", action="store_true", help="Print compact JSON.")
    new_parser.set_defaults(func=cmd_new)

    state_parser = subparsers.add_parser("state", help="Read board state, pieces, FEN, PGN, and outcome.")
    add_position_args(state_parser)
    state_parser.set_defaults(func=cmd_state)

    legal_parser = subparsers.add_parser("legal", help="List legal moves with consequences.")
    add_position_args(legal_parser)
    legal_parser.set_defaults(func=cmd_legal)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a move without changing game state.")
    add_position_args(inspect_parser)
    inspect_parser.add_argument("--move", required=True, help="Move in UCI or SAN, e.g. e2e4 or Nf3.")
    inspect_parser.set_defaults(func=cmd_inspect)

    apply_parser = subparsers.add_parser("apply", help="Apply a legal move to a game JSON file.")
    apply_parser.add_argument("--game", required=True, help="Path to a chessctl game JSON file.")
    apply_parser.add_argument("--move", required=True, help="Move in UCI or SAN, e.g. e2e4 or Nf3.")
    apply_parser.add_argument("--compact", action="store_true", help="Print compact JSON.")
    apply_parser.set_defaults(func=cmd_apply)

    outcome_parser = subparsers.add_parser("outcome", help="Report game result and terminal status.")
    add_position_args(outcome_parser)
    outcome_parser.set_defaults(func=cmd_outcome)

    web_parser = subparsers.add_parser("web", help="Serve the browser chessboard for agent-vs-human play.")
    web_parser.add_argument("--game", default="game.json", help="Path to the shared game JSON file.")
    web_parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    web_parser.add_argument("--port", default=8765, type=int, help="Port to bind.")
    web_parser.add_argument(
        "--engine-color",
        default="black",
        choices=["white", "black"],
        help="Engine color for new sessions when metadata is absent.",
    )
    web_parser.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ChessCtlError as exc:
        payload = {"error": str(exc), **exc.payload}
        compact = bool(getattr(args, "compact", False))
        print(json.dumps(payload, ensure_ascii=False, indent=None if compact else 2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
