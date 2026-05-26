# Chess Agent Instructions

You are playing chess by calling `chessctl`. Treat `chessctl` as the only source of truth for board state, move legality, captures, and game outcome.

## Mandatory Move Flow

1. Read the current state:
   `uv run chessctl state --game game.json`
2. Read legal moves:
   `uv run chessctl legal --game game.json`
3. Choose only one move whose `uci` appears in `legal_moves`.
4. Inspect the exact move before applying it:
   `uv run chessctl inspect --game game.json --move <uci-or-san>`
5. If `legal` is false, do not play that move. Pick another move from `legal_moves`.
6. If the reason for a candidate move depends on the opponent not having a reply, verify that reply from the candidate `fen_after` before applying:
   `uv run chessctl legal --fen '<fen-after>'`
   `uv run chessctl inspect --fen '<fen-after>' --move <opponent-reply>`
7. Apply the final move:
   `uv run chessctl apply --game game.json --move <uci-or-san>`
8. If `outcome.is_game_over` is true, report the result instead of choosing a move.

## Rules

- Never infer legality from memory or chess intuition.
- Never edit `game.json` manually.
- Never apply a move that was not first returned by `chessctl legal`.
- Prefer forcing candidate moves first: checkmate, check, winning capture, promotion, direct threat.
- Avoid obviously hanging the king, queen, or rook unless there is immediate tactical compensation visible from inspected candidate moves.
- When uncertain, inspect multiple candidate moves before applying one.
- Never claim that a tactic is safe because a piece is protected, a capture is illegal, or a reply is unavailable without checking the opponent's legal replies from the candidate `fen_after`.

## Long-Term Plans

- If a plan spans multiple turns, sessions, features, or strategic game phases, save it as Markdown under `plans/`.
- Use a descriptive filename such as `plans/2026-05-26-tal-style-agent.md`.
- Keep the plan concrete: goal, current state, assumptions, next actions, verification steps, and open questions.
- Update the same plan file when continuing the same long-term thread instead of scattering duplicate notes.
- Do not create a plan file for a single immediate move unless the user asks for it.

## GUI Mode

Run the browser board with:

`uv run chessctl web --game game.json --host 127.0.0.1 --port 8765`

The GUI calls the same rules runtime. A human move submitted from the board is validated by the server and then the server waits. Do not use automatic replies during an agent-vs-human game; inspect the position with `chessctl`, explain the intended move, then apply the reply through the CLI.
