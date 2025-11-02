"use strict";
// --- Game.ts ---
Object.defineProperty(exports, "__esModule", { value: true });
exports.Game = exports.Board = exports.Tile = exports.PlayerSymbol = exports.Position = void 0;
// Position and Symbol definitions
class Position {
    constructor(x, y) {
        this.x = x;
        this.y = y;
    }
}
exports.Position = Position;
var PlayerSymbol;
(function (PlayerSymbol) {
    PlayerSymbol["X"] = "X";
    PlayerSymbol["O"] = "O";
    PlayerSymbol["None"] = " ";
})(PlayerSymbol || (exports.PlayerSymbol = PlayerSymbol = {}));
class Tile {
    constructor(position) {
        this.position = position;
        this.symbol = PlayerSymbol.None;
    }
}
exports.Tile = Tile;
class Board {
    constructor() {
        this.tiles = [];
        for (let i = 0; i < 3; i++)
            for (let j = 0; j < 3; j++)
                this.tiles.push(new Tile(new Position(i, j)));
    }
    getTileAt(pos) {
        const found = this.tiles.find(t => t.position.x === pos.x && t.position.y === pos.y);
        if (!found)
            throw new Error('Invalid position');
        return found;
    }
    isEmpty(pos) {
        return this.getTileAt(pos).symbol === PlayerSymbol.None;
    }
    placeSymbol(pos, symbol) {
        this.getTileAt(pos).symbol = symbol;
    }
    winLines() {
        return [
            // Rows
            [new Position(0, 0), new Position(0, 1), new Position(0, 2)],
            [new Position(1, 0), new Position(1, 1), new Position(1, 2)],
            [new Position(2, 0), new Position(2, 1), new Position(2, 2)],
            // Columns
            [new Position(0, 0), new Position(1, 0), new Position(2, 0)],
            [new Position(0, 1), new Position(1, 1), new Position(2, 1)],
            [new Position(0, 2), new Position(1, 2), new Position(2, 2)],
            // Diagonals
            [new Position(0, 0), new Position(1, 1), new Position(2, 2)],
            [new Position(0, 2), new Position(1, 1), new Position(2, 0)],
        ];
    }
    winnerSymbol() {
        for (const line of this.winLines()) {
            const symbols = line.map(pos => this.getTileAt(pos).symbol);
            if (symbols[0] !== PlayerSymbol.None &&
                symbols.every(sym => sym === symbols[0])) {
                return symbols[0];
            }
        }
        return null;
    }
    // return a 3x3 matrix of symbols for easy inspection/printing
    toMatrix() {
        const mat = [];
        for (let i = 0; i < 3; i++) {
            const row = [];
            for (let j = 0; j < 3; j++) {
                row.push(this.getTileAt(new Position(i, j)).symbol);
            }
            mat.push(row);
        }
        return mat;
    }
}
exports.Board = Board;
class Game {
    constructor() {
        this.lastSymbol = PlayerSymbol.None;
        this.board = new Board();
    }
    play(symbol, pos) {
        if (this.lastSymbol === PlayerSymbol.None && symbol === PlayerSymbol.O)
            throw new Error("Invalid first player");
        if (symbol === this.lastSymbol)
            throw new Error("Invalid next player");
        if (!this.board.isEmpty(pos))
            throw new Error("Invalid position");
        this.board.placeSymbol(pos, symbol);
        this.lastSymbol = symbol;
    }
    winner() {
        return this.board.winnerSymbol();
    }
    // expose the current board as a symbol matrix
    getBoardSymbols() {
        return this.board.toMatrix();
    }
}
exports.Game = Game;
