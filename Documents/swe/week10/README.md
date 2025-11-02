## README.md

## 1. Duplicated Code

**Original:**  
Game logic checked each winning row by duplicating code for every row, and would have needed further duplication for columns and diagonals.  
*Location: Game.ts, lines 29–58.*

```typescript
// Example of duplicated win check per row
if (this._board.TileAt(0, 0).Symbol != ' ' && ... ) { ... }
if (this._board.TileAt(1, 0).Symbol != ' ' && ... ) { ... }
if (this._board.TileAt(2, 0).Symbol != ' ' && ... ) { ... }
```

**Refactoring:**  
Created a list of all possible winning lines (rows, columns, diagonals) and checked all in a loop using concise code in `Board.winLines` and `Board.winnerSymbol`.

**Benefit:**  
One clear place for win logic. Supports any board size or win patterns. Easy to add columns or diagonals without duplication.

***

## 2. Primitive Obsession & Data Clump

**Original:**  
Critical code passed X/Y coordinates and symbols as raw numbers and strings everywhere, causing confusion and bugs.  
*Location: Throughout Game.ts, methods and interface definitions.*

```typescript
public Play(symbol: string, x: number, y: number): void
```

**Refactoring:**  
Introduced `Position` and `PlayerSymbol` objects/enums. All moves, tiles, and board queries now use those classes.

**Benefit:**  
Reduces bugs from misordered or invalid data, clarifies code, and makes all API calls explicit and type-safe.

***

## 3. Long Methods & Feature Envy

**Original:**  
Functions like `Winner()` and `Play()` contained game and board logic inline, reaching too deep into other objects and growing long and complex.  
*Location: Game.ts, `Winner()` method.*

**Refactoring:**  
Split logic into small helpers (in Board and Game). Board handles all tile and win logic.

**Benefit:**  
Improves readability and testability. Each class owns its concerns, lowering maintenance cost.

***

## 4. Message Chain

**Original:**  
`this._board.TileAt(x, y).Symbol` appeared everywhere, exposing class internals.  
*Location: Multiple lines.^*

**Refactoring:**  
Created simple getters (`Board.isEmpty`, `Board.getTileAt`, etc.) and ensured Game only calls clean, public API surfaces.

**Benefit:**  
Less coupling, more robust code. Board implementation details are hidden.

***

## 5. Long Parameter List

**Original:**  
Methods accepted many parameters that always travel together.  
*Location: Play, TileAt, AddTileAt, etc.*

**Refactoring:**  
Game and Board methods accept `Position` and, when needed, `PlayerSymbol` only.

**Benefit:**  
Makes function calls clear and expressive while preventing bugs caused by order or count mismatches.

***

## Optimized Files Included

- **Game.ts** — All logic separated into value objects and clear APIs
- **Game.test.ts** — Optimized tests for all win and error conditions

***

## How To Use

```typescript
import { Game, PlayerSymbol, Position } from "./Game";

const game = new Game();
game.play(PlayerSymbol.X, new Position(0, 0));
// Continue as needed for TicTacToe moves...
```

