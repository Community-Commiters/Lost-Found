#!/usr/bin/env node
"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
const readline = __importStar(require("readline"));
const Game_1 = require("./Game");
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const game = new Game_1.Game();
function printBoard() {
    const mat = game.getBoardSymbols();
    console.log('\n  0 1 2');
    for (let i = 0; i < mat.length; i++) {
        const row = mat[i].map(s => (s === Game_1.PlayerSymbol.None ? '.' : s)).join(' ');
        console.log(`${i} ${row}`);
    }
}
let current = Game_1.PlayerSymbol.X;
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
            game.play(current, new Game_1.Position(r, c));
        }
        catch (err) {
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
        current = current === Game_1.PlayerSymbol.X ? Game_1.PlayerSymbol.O : Game_1.PlayerSymbol.X;
        askMove();
    });
}
console.log('Tic-Tac-Toe — play in terminal');
askMove();
