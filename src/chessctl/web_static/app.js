const boardEl = document.querySelector("#board");
const statusLineEl = document.querySelector("#status-line");
const turnEl = document.querySelector("#turn");
const humanColorEl = document.querySelector("#human-color");
const engineColorEl = document.querySelector("#engine-color");
const legalCountEl = document.querySelector("#legal-count");
const resultEl = document.querySelector("#result");
const lastTurnEl = document.querySelector("#last-turn");
const moveListEl = document.querySelector("#move-list");
const manualFormEl = document.querySelector("#manual-form");
const manualMoveEl = document.querySelector("#manual-move");
const newWhiteEl = document.querySelector("#new-white");
const newBlackEl = document.querySelector("#new-black");
const promotionDialogEl = document.querySelector("#promotion-dialog");
const promotionOptionsEl = document.querySelector("#promotion-options");
const promotionCancelEl = document.querySelector("#promotion-cancel");

const FILES = ["a", "b", "c", "d", "e", "f", "g", "h"];
const WHITE_RANKS = [8, 7, 6, 5, 4, 3, 2, 1];
const BLACK_RANKS = [1, 2, 3, 4, 5, 6, 7, 8];
const PROMOTION_ORDER = ["queen", "rook", "bishop", "knight"];
const PIECE_CODES = {
  K: 0x2654,
  Q: 0x2655,
  R: 0x2656,
  B: 0x2657,
  N: 0x2658,
  P: 0x2659,
  k: 0x265a,
  q: 0x265b,
  r: 0x265c,
  b: 0x265d,
  n: 0x265e,
  p: 0x265f,
};

let snapshot = null;
let selectedSquare = null;
let lastMoveSquares = [];
let pendingPromotionMoves = [];
let promotionReturnFocus = null;

function pieceGlyph(symbol) {
  return String.fromCodePoint(PIECE_CODES[symbol] || 0x25a1);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "Request failed");
    error.data = data;
    throw error;
  }
  return data;
}

function pieceBySquare(state) {
  const pieces = new Map();
  for (const piece of state.state.pieces) {
    pieces.set(piece.square, piece);
  }
  return pieces;
}

function boardFiles() {
  return snapshot && snapshot.human_color === "black" ? [...FILES].reverse() : FILES;
}

function boardRanks() {
  return snapshot && snapshot.human_color === "black" ? BLACK_RANKS : WHITE_RANKS;
}

function isHumanTurn() {
  if (!snapshot) return false;
  return snapshot.state.turn === snapshot.human_color && !snapshot.outcome.is_game_over;
}

function legalFrom(square) {
  if (!snapshot) return [];
  return snapshot.legal_moves.filter((move) => move.from === square);
}

function legalTo(from, to) {
  return legalFrom(from).filter((move) => move.to === to);
}

function squareColor(fileIndex, rank) {
  const rankIndex = rank - 1;
  return (fileIndex + rankIndex) % 2 === 0 ? "dark" : "light";
}

function promotionRank(move) {
  const index = PROMOTION_ORDER.indexOf(move.promotion);
  return index === -1 ? PROMOTION_ORDER.length : index;
}

function promotionLabel(promotion) {
  return `${promotion[0].toUpperCase()}${promotion.slice(1)}`;
}

function promotionGlyph(move) {
  const symbols = {
    queen: "q",
    rook: "r",
    bishop: "b",
    knight: "n",
  };
  const symbol = symbols[move.promotion] || "q";
  return pieceGlyph(move.piece.color === "white" ? symbol.toUpperCase() : symbol);
}

function closePromotionDialog() {
  promotionDialogEl.hidden = true;
  promotionOptionsEl.innerHTML = "";
  pendingPromotionMoves = [];
  if (promotionReturnFocus && document.body.contains(promotionReturnFocus)) {
    promotionReturnFocus.focus();
  }
  promotionReturnFocus = null;
}

function openPromotionDialog(moves) {
  pendingPromotionMoves = moves
    .filter((move) => move.promotion)
    .sort((left, right) => promotionRank(left) - promotionRank(right));
  promotionOptionsEl.innerHTML = "";

  for (const move of pendingPromotionMoves) {
    const button = document.createElement("button");
    const label = promotionLabel(move.promotion);
    button.type = "button";
    button.className = "promotion-option";
    button.dataset.move = move.uci;
    button.title = label;
    button.setAttribute("aria-label", `Promote to ${label}`);

    const piece = document.createElement("span");
    piece.className = `piece ${move.piece.color}`;
    piece.textContent = promotionGlyph(move);
    button.appendChild(piece);

    button.addEventListener("click", () => {
      closePromotionDialog();
      playMove(move.uci);
    });
    promotionOptionsEl.appendChild(button);
  }

  promotionReturnFocus = document.activeElement;
  promotionDialogEl.hidden = false;
  promotionOptionsEl.querySelector("button")?.focus();
}

async function playMove(move) {
  setBusy(true);
  try {
    const next = await api("/api/move", {
      method: "POST",
      body: JSON.stringify({ move }),
    });
    selectedSquare = null;
    const applied = next.applied_moves || [];
    const last = applied[applied.length - 1];
    lastMoveSquares = last ? [last.from, last.to] : [];
    render(next, applied);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

function handleSquareClick(square, piece) {
  if (!isHumanTurn()) return;

  if (selectedSquare) {
    const moves = legalTo(selectedSquare, square);
    if (moves.length > 0) {
      if (moves.some((move) => move.promotion)) {
        openPromotionDialog(moves);
      } else {
        playMove(moves[0].uci);
      }
      return;
    }
  }

  if (piece && piece.color === snapshot.human_color && legalFrom(square).length > 0) {
    selectedSquare = square;
  } else {
    selectedSquare = null;
  }
  renderBoard();
}

function renderBoard() {
  if (!snapshot) return;

  const pieces = pieceBySquare(snapshot);
  const files = boardFiles();
  const ranks = boardRanks();
  const targets = selectedSquare ? legalFrom(selectedSquare) : [];
  const targetSquares = new Map(targets.map((move) => [move.to, move]));

  boardEl.innerHTML = "";

  for (const rank of ranks) {
    files.forEach((file, fileIndex) => {
      const square = `${file}${rank}`;
      const piece = pieces.get(square);
      const button = document.createElement("button");
      button.type = "button";
      button.className = `square ${squareColor(FILES.indexOf(file), rank)}`;
      button.dataset.square = square;
      button.setAttribute("aria-label", square);

      if (selectedSquare === square) button.classList.add("selected");
      if (lastMoveSquares.includes(square)) button.classList.add("last");
      if (targetSquares.has(square)) {
        button.classList.add("target");
        if (targetSquares.get(square).capture.is_capture) button.classList.add("capture");
      }

      if (piece) {
        const span = document.createElement("span");
        span.className = `piece ${piece.color}`;
        span.textContent = pieceGlyph(piece.symbol);
        button.appendChild(span);
      }

      if ((files[0] === file || rank === ranks[ranks.length - 1]) && boardEl.clientWidth > 360) {
        const coord = document.createElement("span");
        coord.className = "coord";
        coord.textContent = files[0] === file ? square : file;
        button.appendChild(coord);
      }

      button.addEventListener("click", () => handleSquareClick(square, piece));
      boardEl.appendChild(button);
    });
  }
}

function renderFacts() {
  const outcome = snapshot.outcome;
  turnEl.textContent = snapshot.state.turn;
  humanColorEl.textContent = snapshot.human_color;
  engineColorEl.textContent = snapshot.engine_color;
  legalCountEl.textContent = String(snapshot.state.legal_move_count);
  resultEl.textContent = outcome.result === "*" ? "in progress" : outcome.result;

  if (outcome.is_game_over) {
    statusLineEl.textContent = `Game over: ${outcome.result}`;
  } else if (snapshot.is_engine_turn) {
    statusLineEl.textContent = "Opponent to move. Waiting for reply.";
  } else if (isHumanTurn()) {
    statusLineEl.textContent = "Your turn. Select a piece, then a legal target square.";
  } else {
    statusLineEl.textContent = "Waiting for position update.";
  }
}

function renderMoves() {
  const moves = snapshot.state.game.moves || [];
  moveListEl.innerHTML = "";
  for (let index = 0; index < moves.length; index += 2) {
    const li = document.createElement("li");
    const white = moves[index] || "";
    const black = moves[index + 1] || "";
    li.textContent = black ? `${white} ${black}` : white;
    moveListEl.appendChild(li);
  }
}

function renderLastTurn(appliedMoves = []) {
  if (!appliedMoves.length) {
    if (!snapshot.state.game.moves.length) {
      lastTurnEl.textContent = "No moves yet.";
    }
    return;
  }

  const lines = appliedMoves.map((move, index) => {
    const actor = move.piece.color === snapshot.human_color ? "You" : "Opponent";
    const capture = move.capture && move.capture.is_capture ? " capture" : "";
    const check = move.gives_checkmate ? " checkmate" : move.gives_check ? " check" : "";
    return `${actor}: ${move.san}${capture}${check}`;
  });
  lastTurnEl.textContent = lines.join(" / ");
}

function render(nextSnapshot, appliedMoves = []) {
  snapshot = nextSnapshot;
  renderBoard();
  renderFacts();
  renderMoves();
  renderLastTurn(appliedMoves);
}

function showError(message) {
  statusLineEl.innerHTML = "";
  const span = document.createElement("span");
  span.className = "error";
  span.textContent = message;
  statusLineEl.appendChild(span);
}

function setBusy(isBusy) {
  for (const button of document.querySelectorAll("button")) {
    button.disabled = isBusy;
  }
  manualMoveEl.disabled = isBusy;
}

async function loadState() {
  setBusy(true);
  try {
    const data = await api("/api/state");
    closePromotionDialog();
    render(data);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function newGame(humanColor) {
  setBusy(true);
  try {
    const data = await api("/api/new", {
      method: "POST",
      body: JSON.stringify({ human_color: humanColor }),
    });
    selectedSquare = null;
    closePromotionDialog();
    const applied = data.applied_moves || [];
    const last = applied[applied.length - 1];
    lastMoveSquares = last ? [last.from, last.to] : [];
    render(data, applied);
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

manualFormEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const move = manualMoveEl.value.trim();
  if (!move) return;
  manualMoveEl.value = "";
  playMove(move);
});

newWhiteEl.addEventListener("click", () => newGame("white"));
newBlackEl.addEventListener("click", () => newGame("black"));
promotionCancelEl.addEventListener("click", closePromotionDialog);
promotionDialogEl.addEventListener("click", (event) => {
  if (event.target === promotionDialogEl) closePromotionDialog();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !promotionDialogEl.hidden) closePromotionDialog();
});
window.addEventListener("resize", renderBoard);

loadState();
