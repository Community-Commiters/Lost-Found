#!/usr/bin/env node
import * as readline from 'readline';
import { Game, PlayerSymbol, Position } from './Game';

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

const game = new Game();

function printBoard() {
  const mat = game.getBoardSymbols();
  console.log('\n  0 1 2');
  for (let i = 0; i < mat.length; i++) {
    const row = mat[i].map(s => (s === PlayerSymbol.None ? '.' : s)).join(' ');
    console.log(`${i} ${row}`);
  }
}

let current = PlayerSymbol.X;

function askMove() {
  printBoard();
  rl.question(`\nPlayer ${current} - enter move as 'row,col' (e.g. 0,2) or 'q' to quit: `, ans => {
    if (ans.trim().toLowerCase() === 'q') {
      console.log('Goodbye');
      rl.close();
      return;
    }

    const parts = ans.split(',').map(p => p.trim());
    if (parts.length !== 2) {
      console.log('Invalid input. Use row,col');
      askMove();
      return;
    }
    const r = Number(parts[0]);
    const c = Number(parts[1]);
    if (!Number.isInteger(r) || !Number.isInteger(c) || r < 0 || r > 2 || c < 0 || c > 2) {
      console.log('Invalid coordinates. Rows and columns must be 0, 1, or 2.');
      askMove();
      return;
    }

    try {
      game.play(current, new Position(r, c));
    } catch (err: any) {
      console.log('Move rejected:', err.message || err);
      askMove();
      return;
    }

    const winner = game.winner();
    if (winner) {
      printBoard();
      console.log(`\nPlayer ${winner} wins!`);
      rl.close();
      return;
    }

    // switch player
    current = current === PlayerSymbol.X ? PlayerSymbol.O : PlayerSymbol.X;
    askMove();
  });
}

console.log('Tic-Tac-Toe — play in terminal');
askMove();
