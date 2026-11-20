"use strict";
const start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR";
const symbols = {k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟"};
const board = document.querySelector("#board");
const form = document.querySelector("#form");
const input = document.querySelector("#position");
const statusText = document.querySelector("#status");
const move = document.querySelector("#move");
const stats = document.querySelector("#stats");

function draw(position, bestmove = "") {
  const rows = (position === "startpos" ? start : position.split(" ")[0]).split("/");
  const squares = document.createDocumentFragment();
  rows.forEach((row, rank) => {
    const pieces = row.replace(/[1-8]/g, (digit) => " ".repeat(Number(digit)));
    Array.from(pieces).forEach((piece, file) => {
      const square = document.createElement("span");
      const coordinate = "abcdefgh"[file] + (8 - rank);
      square.classList.add("square", (rank + file) % 2 ? "dark" : "light");
      if (symbols[piece.toLowerCase()]) {
        square.textContent = symbols[piece.toLowerCase()];
        square.classList.add(piece === piece.toLowerCase() ? "black-piece" : "white-piece");
      }
      if ([bestmove.slice(0, 2), bestmove.slice(2, 4)].includes(coordinate)) {
        square.classList.add("highlight");
      }
      squares.append(square);
    });
  });
  board.replaceChildren(squares);
  board.setAttribute("aria-label", "Analyzed position: " + position + (bestmove ? ". Best move: " + bestmove : ""));
}

draw("startpos");
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = form.querySelector("button");
  const position = input.value.trim();
  button.disabled = true;
  input.readOnly = true;
  statusText.textContent = "Searching…";
  move.textContent = "";
  stats.textContent = "";
  try {
    const response = await fetch("/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({position})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Analysis failed.");
    draw(position, result.bestmove);
    const playable = !["0000", "(none)"].includes(result.bestmove);
    statusText.textContent = playable ? "Best move" : "No legal moves";
    move.textContent = playable ? result.bestmove : "";
    stats.textContent = [result.engine, result.depth && "Depth " + result.depth,
      result.nodes && result.nodes.toLocaleString() + " nodes"].filter(Boolean).join(" · ");
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    input.readOnly = false;
    button.disabled = false;
  }
});
