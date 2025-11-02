// --- Game.ts ---

// Position and Symbol definitions
export class Position {
  constructor(public x: number, public y: number) {}
}

export enum PlayerSymbol {
  X = "X",
  O = "O",
  None = " "
}

export class Tile {
  symbol: PlayerSymbol = PlayerSymbol.None;
  constructor(public position: Position) {}
}

export class Board {
  private tiles: Tile[] = [];
  constructor() {
    for (let i = 0; i < 3; i++)
      for (let j = 0; j < 3; j++)
        this.tiles.push(new Tile(new Position(i, j)));
  }

  getTileAt(pos: Position): Tile {
    const found = this.tiles.find(
      t => t.position.x === pos.x && t.position.y === pos.y
    );
    if (!found) throw new Error('Invalid position');
    return found;
  }

  isEmpty(pos: Position): boolean {
    return this.getTileAt(pos).symbol === PlayerSymbol.None;
  }

  placeSymbol(pos: Position, symbol: PlayerSymbol): void {
    this.getTileAt(pos).symbol = symbol;
  }

  winLines(): Position[][] {
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

  winnerSymbol(): PlayerSymbol | null {
    for (const line of this.winLines()) {
      const symbols = line.map(pos => this.getTileAt(pos).symbol);
      if (
        symbols[0] !== PlayerSymbol.None &&
        symbols.every(sym => sym === symbols[0])
      ) {
        return symbols[0];
      }
    }
    return null;
  }

  // return a 3x3 matrix of symbols for easy inspection/printing
  toMatrix(): PlayerSymbol[][] {
    const mat: PlayerSymbol[][] = [];
    for (let i = 0; i < 3; i++) {
      const row: PlayerSymbol[] = [];
      for (let j = 0; j < 3; j++) {
        row.push(this.getTileAt(new Position(i, j)).symbol);
      }
      mat.push(row);
    }
    return mat;
  }
}

export class Game {
  private lastSymbol: PlayerSymbol = PlayerSymbol.None;
  private board: Board = new Board();

  play(symbol: PlayerSymbol, pos: Position): void {
    if (this.lastSymbol === PlayerSymbol.None && symbol === PlayerSymbol.O)
      throw new Error("Invalid first player");
    if (symbol === this.lastSymbol)
      throw new Error("Invalid next player");
    if (!this.board.isEmpty(pos))
      throw new Error("Invalid position");

    this.board.placeSymbol(pos, symbol);
    this.lastSymbol = symbol;
  }

  winner(): PlayerSymbol | null {
    return this.board.winnerSymbol();
  }

  // expose the current board as a symbol matrix
  getBoardSymbols(): PlayerSymbol[][] {
    return this.board.toMatrix();
  }
}
