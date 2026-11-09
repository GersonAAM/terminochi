#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TERMINOCHI - una mascota virtual para la terminal.

Controles: flechas para moverte, Enter para seleccionar, Esc para volver.
La partida se guarda sola y la mascota solo "vive" mientras el juego esta abierto.
Pon canciones (.mp3, .wav...) en la carpeta "music" para que tu mascota baile.
"""

import functools
import json
import math
import os
import random
import re
import shutil
import sys
import textwrap
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_FILE = os.path.join(BASE_DIR, "terminochi_save.json")
MUSIC_DIR = os.path.join(BASE_DIR, "music")

IS_WINDOWS = os.name == "nt"

# --------------------------------------------------------------------------
# Terminal: colores, teclado y dibujo
# --------------------------------------------------------------------------

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
INVERSE = "\x1b[7m"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@functools.lru_cache(maxsize=None)
def fg(n):
    return f"\x1b[38;5;{n}m"


@functools.lru_cache(maxsize=None)
def bg(n):
    return f"\x1b[48;5;{n}m"


COL = {
    "border": fg(110), "title": fg(219) + BOLD, "text": fg(253), "dim": fg(244),
    "sun": fg(220), "cloud": fg(255), "hill": fg(108), "grass": fg(70), "grass2": fg(34),
    "grass3": fg(112), "dirt": fg(28), "leaf": fg(34), "leaf2": fg(28), "trunk": fg(130),
    "fence": fg(180), "stem": fg(28), "apple": fg(196),
    "wall": fg(239), "wood": fg(137), "wood2": fg(94), "floor": fg(137),
    "lamp": fg(222), "rug": fg(168), "curtain": fg(175), "plant": fg(35), "pot": fg(166),
    "pillow": fg(255), "night": bg(17), "star": bg(17) + fg(229), "mount": fg(109),
    "heart": fg(204), "ball": fg(196), "zzz": fg(117), "blush": fg(211), "shadow": fg(240),
    "butterfly": fg(213),
}
PARTY = [196, 208, 226, 46, 51, 21, 201, 213]  # colores de fiesta


def vis_len(s):
    return len(ANSI_RE.sub("", s))


def pad(s, width):
    return s + " " * max(0, width - vis_len(s))


if IS_WINDOWS:
    import msvcrt

    class KeyReader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_key(self, timeout):
            end = time.monotonic() + timeout
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        code = msvcrt.getwch()
                        return {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}.get(code)
                    return _translate(ch)
                if time.monotonic() >= end:
                    return None
                time.sleep(0.01)
else:
    import select
    import termios
    import tty

    class KeyReader:
        def __enter__(self):
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            return self

        def __exit__(self, *exc):
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
            return False

        def _ready(self, timeout):
            return bool(select.select([self.fd], [], [], timeout)[0])

        def get_key(self, timeout):
            if not self._ready(timeout):
                return None
            b = os.read(self.fd, 1)
            if b == b"\x1b":
                if self._ready(0.03) and os.read(self.fd, 1) in (b"[", b"O"):
                    code = os.read(self.fd, 1)
                    return {b"A": "UP", b"B": "DOWN", b"D": "LEFT", b"C": "RIGHT"}.get(code)
                return "ESC"
            # caracteres UTF-8 de varios bytes (ñ, acentos)
            first = b[0]
            extra = 1 if first >= 0xC0 else 0
            extra += 1 if first >= 0xE0 else 0
            extra += 1 if first >= 0xF0 else 0
            if extra:
                b += os.read(self.fd, extra)
            return _translate(b.decode("utf-8", "ignore"))


def _translate(ch):
    if ch in ("\r", "\n"):
        return "ENTER"
    if ch == "\x1b":
        return "ESC"
    if ch in ("\x08", "\x7f"):
        return "BACKSPACE"
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch


_last_out = None


def draw(lines):
    """Escribe el frame solo si cambio respecto al anterior."""
    global _last_out
    out = "\x1b[H" + "\x1b[K\n".join(lines) + "\x1b[K\x1b[J"
    if out == _last_out:
        return
    _last_out = out
    sys.stdout.write(out)
    sys.stdout.flush()


def clear_screen():
    global _last_out
    _last_out = None
    sys.stdout.write("\x1b[2J")


# --------------------------------------------------------------------------
# Lienzo: matriz de (caracter, color)
# --------------------------------------------------------------------------

SCENE_W, SCENE_H = 100, 17
FLOOR_Y = SCENE_H - 3  # fila donde apoyan las patas


def blank_grid():
    return [[(" ", "")] * SCENE_W for _ in range(SCENE_H)]


def stamp(grid, lines, x, y, color, accents=None):
    """Dibuja lines sobre grid; los espacios de los bordes de cada linea son
    transparentes. accents asigna colores propios a ciertos caracteres."""
    for i, line in enumerate(lines):
        row = y + i
        if not 0 <= row < SCENE_H:
            continue
        body = line.strip(" ")
        start = len(line) - len(line.lstrip(" "))
        cells = grid[row]
        for j, ch in enumerate(body):
            col = x + start + j
            if 0 <= col < SCENE_W:
                cells[col] = (ch, accents.get(ch, color) if accents else color)


def grid_to_lines(grid):
    out = []
    for row in grid:
        parts, cur = [], None
        for ch, color in row:
            if color != cur:
                parts.append(RESET + color)
                cur = color
            parts.append(ch)
        parts.append(RESET)
        out.append("".join(parts))
    return out


# --------------------------------------------------------------------------
# Mascotas: cabeza + cuerpo con poses
# --------------------------------------------------------------------------
# Todas las piezas estan centradas en la columna 7.

PETS = {
    "gatito": {
        "label": "Gatito", "color": 215, "mouth": "w", "feet": "U", "tail": ("~", "S"),
        "sound": "prrr~ prrr~",
        "head": [
            "   /\\_____/\\",
            "  /  {l}   {r}  \\",
            " ( {c}  ={m}=  {c} )",
            "   `-.___.-'",
        ],
        "mouth_row": 2, "back_replace": {"= =": "   "},
    },
    "pollito": {
        "label": "Pollito", "color": 227, "mouth": "v", "feet": "^", "tail": ("", ""),
        "sound": "¡pío pío pío!",
        "head": [
            "      \\|/",
            "    .-'''-.",
            "   /  {l} {r}  \\",
            "  | {c}  {m}  {c} |",
            "   \\_______/",
        ],
        "mouth_row": 3, "back_replace": {},
    },
    "perrito": {
        "label": "Perrito", "color": 180, "mouth": "w", "feet": "U", "tail": ("/", "\\"),
        "sound": "*mueve la colita sin parar*",
        "head": [
            "    _______",
            " .-/       \\-.",
            "( (  {l}   {r}  ) )",
            " \\_|{c} (_) {c}|_/",
            "     \\_{m}_/",
        ],
        "mouth_row": 4, "back_replace": {"(_)": "   "},
    },
}
PET_ORDER = ["gatito", "pollito", "perrito"]

_TOP = ["   .'     '.", "  /|       |\\", " / |       | \\"]
_HIP = "   '._____.'  {T}"
BODIES = {
    "stand": _TOP + [_HIP, "     {F}   {F}"],
    "up": ["\\  .'     '.  /", " \\_|       |_/", "   |       |", _HIP, "     {F}   {F}"],
    "left": ["\\  .'     '.", " \\_|       |\\", "   |       | \\", _HIP, "    {F}     {F}"],
    "right": ["   .'     '.  /", "  /|       |_/", " / |       |", _HIP, "    {F}     {F}"],
    "squat": [" _.'       '._", "/  |       |  \\", _HIP, "   {F}/     \\{F}"],
    "kick_l": _TOP + [_HIP, "  {F}__/   {F}"],
    "kick_r": _TOP + [_HIP, "     {F}   \\__{F}"],
    "walk1": _TOP + [_HIP, "    {F}     {F}"],
    "walk2": _TOP + [_HIP, "      {F} {F}"],
}
PET_W = 16
for _pet in PETS.values():
    _pet["head_h"] = len(_pet["head"])


@functools.lru_cache(maxsize=1024)
def compose_sprite(species, pose, l, r, m, c=" ", tail=0, head_dx=0, back=False):
    """Une cabeza y cuerpo. Devuelve (lineas, desplazamiento_x)."""
    pet = PETS[species]
    if back:
        l = r = m = c = " "
    head = [h.format(l=l, r=r, m=m, c=c) for h in pet["head"]]
    if back:
        for old, new in pet["back_replace"].items():
            head = [h.replace(old, new) for h in head]
    body = [b.format(F=pet["feet"], T=pet["tail"][tail]) for b in BODIES[pose]]
    lpad = max(0, -head_dx)
    head = [" " * (lpad + head_dx) + h for h in head]
    body = [" " * lpad + b for b in body]
    return tuple(head + body), -lpad


# Coreografias de baile: (pose, dx, dy, mover_cabeza, de_espaldas)
DANCES = [
    ("Paso lateral", [("left", -2, 0, -1, 0), ("stand", -4, 0, 0, 0), ("left", -2, 0, -1, 0),
                      ("stand", 0, 0, 0, 0), ("right", 2, 0, 1, 0), ("stand", 4, 0, 0, 0),
                      ("right", 2, 0, 1, 0), ("stand", 0, 0, 0, 0)]),
    ("Saltitos", [("squat", 0, 0, 0, 0), ("up", 0, 2, 0, 0), ("up", 0, 3, 0, 0), ("up", 0, 2, 0, 0),
                  ("squat", 0, 0, 0, 0), ("stand", 0, 0, 0, 0), ("up", 0, 1, 0, 0), ("stand", 0, 0, 0, 0)]),
    ("Giro", [("stand", 0, 0, 0, 0), ("right", 1, 0, 1, 0), ("stand", 0, 1, 0, 1), ("left", -1, 0, -1, 0),
              ("stand", 0, 0, 0, 0), ("up", 0, 1, 0, 0), ("stand", 0, 0, 0, 0), ("squat", 0, 0, 0, 0)]),
    ("Patadas", [("kick_l", 0, 0, -1, 0), ("stand", 0, 0, 0, 0), ("kick_r", 0, 0, 1, 0), ("stand", 0, 0, 0, 0),
                 ("kick_l", -1, 1, -1, 0), ("stand", 0, 0, 0, 0), ("kick_r", 1, 1, 1, 0), ("up", 0, 0, 0, 0)]),
    ("Cabeceo", [("stand", 0, 0, -1, 0), ("stand", 0, 0, 1, 0), ("stand", 0, 0, -1, 0), ("stand", 0, 0, 1, 0),
                 ("up", 0, 0, -1, 0), ("up", 0, 0, 1, 0), ("up", 0, 0, -1, 0), ("up", 0, 0, 1, 0)]),
    ("Moonwalk", [("walk1" if i % 2 else "walk2", -min(i, 16 - i), 0, 1, 0) for i in range(16)]),
    ("Robot", [("left", 0, 0, 0, 0), ("left", 0, 0, 0, 0), ("up", 0, 0, 0, 0), ("up", 0, 0, 0, 0),
               ("right", 0, 0, 0, 0), ("right", 0, 0, 0, 0), ("squat", 0, 0, 0, 0), ("stand", 0, 0, 0, 0)]),
]
DANCE_STEPS = 16  # pasos por coreografia (cada paso dura 1/4 de segundo)
DANCE_FACES = [("^", "^", "D"), (">", "<", "o"), ("^", "^", "v"), ("*", "*", "D"),
               ("o", "o", "O"), ("-", "^", "P"), ("^", "^", "w")]

# --------------------------------------------------------------------------
# Escenarios
# --------------------------------------------------------------------------

TREE = [
    "          &&&&&&&&",
    "     &&&&&&&&&&&&&&&&&&",
    "  &&&&&&&&&&&&&&&&&&&&&&&&",
    " &&&&&&&&&&&&&&&&&&&&&&&&&&",
    "  &&&&&&&&&&&&&&&&&&&&&&&&",
    "     &&&&&&&&&&&&&&&&&&",
    "         &&&&&&&&&&",
]
TRUNK = ["|    |", "| )  |", "|    |", "|  ( |", "|    |", "| )  |", "|    |"]
FENCE = [" _   ", "| |  ", "| |==", "| |  ", "| |==", "|_|  "]

SUN = [
    ["   \\  |  /", "    .---.", "--=( {f} )=--", "    '---'", "   /  |  \\"],
    [" `.   :   .'", "    .---.", "  -( {f} )-", "    '---'", " .'   :   `."],
]
CLOUDS = [  # (dibujo, fila, velocidad, desfase)
    (["      .--.", "   .-(    ).", "  (___.__)__)"], 0, 0.8, 10),
    (["  .-~~~-.", " (       )-.", "(___________)"], 1, 0.5, 60),
    (["   .-.", " .(   ).", "(___.__)"], 0, 0.65, 95),
]
FLOWERS = [(3, "@", 205), (9, "*", 196), (16, "o", 226), (23, "@", 213), (30, "*", 203),
           (64, "o", 207), (70, "@", 220), (77, "*", 141), (93, "@", 205)]

WINDOW = [
    "o=========================o",
    " ))|         |         |(( ",
    " ))|         |         |(( ",
    " ) |         |         | ( ",
    " ) |---------+---------| ( ",
    " ) |         |         | ( ",
    " ))|         |         |(( ",
    " ))|_________|_________|(( ",
    "   '=====================' ",
]
WINDOW_X, WINDOW_Y = 2, 1
STARS = [(6, 1), (10, 2), (16, 3), (21, 1), (8, 5), (18, 6), (11, 6), (5, 3), (15, 5)]
PICTURE = [".-----------.", "|    /\\   o |", "|   /  \\/\\  |", "|  /    \\ \\ |", "'-----------'"]
CLOCK = ["  .-----.", " /  12   \\", "|9   o   3|", " \\   6   /", "  '-----'"]
CLOCK_X, CLOCK_Y = 62, 1
CLOCK_HANDS = [(0, -1, "|"), (1, -1, "/"), (1, 0, "-"), (1, 1, "\\"),
               (0, 1, "|"), (-1, 1, "/"), (-1, 0, "-"), (-1, -1, "\\")]
BED = [
    " ___",
    "|   |" + "_" * 23 + " ",
    "|   | .-----.  " + "~" * 12 + " |",
    "|   | '-----'  " + "~" * 12 + " |",
    "|   |" + "_" * 23 + "|",
    "|___|" + " " * 21 + "|_|",
]
LAMP = [" _ ", "/_\\"] + [" | "] * 6 + ["_|_"]
DISCO = [["  |", " .-.", "(#:#)", " '-'"], ["  |", " .-.", "(:#:)", " '-'"]]


def hills_lines():
    rows = [[" "] * SCENE_W for _ in range(SCENE_H)]
    h = [int(round(6 - 1.3 * math.sin(x / 11) - 0.8 * math.sin(x / 5 + 1))) for x in range(SCENE_W + 1)]
    for x in range(SCENE_W):
        a, b = h[x], h[x + 1]
        if b < a:
            rows[a][x] = "/"
        elif b > a:
            rows[b][x] = "\\"
        else:
            rows[a][x] = "_"
        for y in range(max(a, b) + 1, FLOOR_Y - 5):
            if (x * 7 + y * 13) % 11 == 0:
                rows[y][x] = "."
    return ["".join(r) for r in rows]


def build_garden():
    grid = blank_grid()
    rng = random.Random(7)
    stamp(grid, hills_lines(), 0, 0, COL["hill"])
    stamp(grid, [u * 7 for u in FENCE], 0, FLOOR_Y - 5, COL["fence"])
    stamp(grid, [u * 8 for u in FENCE], 62, FLOOR_Y - 5, COL["fence"])
    leaves = ["".join("%" if ch == "&" and (i * 3 + r * 5) % 7 == 0 else ch for i, ch in enumerate(line))
              for r, line in enumerate(TREE)]
    stamp(grid, leaves, 72, 0, COL["leaf"], {"%": COL["leaf2"]})
    for ax, ay in ((78, 2), (90, 3), (84, 4), (95, 3), (80, 5)):
        stamp(grid, ["o"], ax, ay, COL["apple"])
    stamp(grid, TRUNK, 83, 7, COL["trunk"])
    stamp(grid, ["/      \\"], 82, FLOOR_Y, COL["trunk"])
    grass = "".join(rng.choice("wWwvW") for _ in range(SCENE_W))
    dirt = "".join(rng.choice(",.'` ,") for _ in range(SCENE_W))
    stamp(grid, [grass], 0, FLOOR_Y + 1, COL["grass"], {"W": COL["grass2"], "v": COL["grass3"]})
    stamp(grid, [dirt], 0, FLOOR_Y + 2, COL["dirt"])
    return grid


def build_room():
    grid = blank_grid()
    for r in range(FLOOR_Y + 1):
        for c in range(SCENE_W):
            if (r % 4 == 1 and c % 8 == 3) or (r % 4 == 3 and c % 8 == 7):
                grid[r][c] = (".", COL["wall"])
    stamp(grid, WINDOW, WINDOW_X, WINDOW_Y, COL["wood"],
          {")": COL["curtain"], "(": COL["curtain"], "o": COL["wood2"], "=": COL["wood2"]})
    for dy in (1, 2, 3, 5, 6):  # cielo nocturno dentro de la ventana
        for dx in list(range(4, 13)) + list(range(14, 23)):
            grid[WINDOW_Y + dy][WINDOW_X + dx] = (" ", COL["night"])
    grid[WINDOW_Y + 2][WINDOW_X + 19] = ("C", COL["star"])
    stamp(grid, PICTURE, 32, 2, COL["wood"], {"/": COL["mount"], "\\": COL["mount"], "o": COL["sun"]})
    stamp(grid, CLOCK, CLOCK_X, CLOCK_Y, COL["wood"],
          {d: fg(250) for d in "123456789"} | {"o": COL["lamp"]})
    stamp(grid, BED, 68, FLOOR_Y - 5, COL["wood"],
          {"~": COL["rug"], ".": COL["pillow"], "'": COL["pillow"], "-": COL["pillow"]})
    stamp(grid, LAMP, 97, FLOOR_Y - 8, COL["wood"], {"_": COL["lamp"], "/": COL["lamp"], "\\": COL["lamp"]})
    stamp(grid, ["  \\ | /", " `.\\|/.'"], 0, FLOOR_Y - 4, COL["plant"])
    stamp(grid, ["  (_____)", "   |   |", "   |___|"], 0, FLOOR_Y - 2, COL["pot"])
    stamp(grid, [".----------.", "| JUGUETES |", "|__________|"], 11, FLOOR_Y - 2, COL["wood"],
          {ch: COL["lamp"] for ch in "JUGETS"})
    stamp(grid, ["O"], 14, FLOOR_Y - 3, COL["ball"])
    stamp(grid, ["[A]"], 18, FLOOR_Y - 3, fg(75))
    stamp(grid, ["=" * SCENE_W], 0, FLOOR_Y + 1, COL["floor"])
    stamp(grid, ["".join("|" if c % 12 == 0 else "_" for c in range(SCENE_W))], 0, FLOOR_Y + 2, COL["wood2"])
    stamp(grid, ["." + "-~" * 19 + "-.", "'" + "~-" * 19 + "~'"], 30, FLOOR_Y + 1, COL["rug"])
    return grid


SCENES = {"jardin": build_garden(), "habitacion": build_room()}
PLACES = {"jardin": "Jardín", "habitacion": "Habitación"}

# --------------------------------------------------------------------------
# Comida y reacciones
# --------------------------------------------------------------------------

# (nombre, tipo, color, cuanto llena)
FOODS = [
    ("Manzana", "fruta", 196, 12), ("Plátano", "fruta", 226, 14),
    ("Fresa", "fruta", 197, 10), ("Uva", "fruta", 135, 8),
    ("Sandía", "fruta", 203, 15), ("Mango", "fruta", 214, 13),
    ("Piña", "fruta", 220, 12), ("Naranja", "fruta", 208, 12),
    ("Cereza", "fruta", 160, 8), ("Kiwi", "fruta", 112, 10),
    ("Zanahoria", "verdura", 208, 18), ("Brócoli", "verdura", 34, 20),
    ("Lechuga", "verdura", 120, 15), ("Pepino", "verdura", 71, 16),
    ("Calabaza", "verdura", 172, 22),
]
FOOD_LABELS = [f[0] for f in FOODS] + ["Volver"]

# 20 reacciones: id -> (texto, ojo izq, ojo der, boca, felicidad, energia)
REACTIONS = {
    "yum": ("¡Ñam ñam! ¡Delicioso!", "^", "^", "D", 8, 0),
    "fav": ("¡¡Es mi favorita!! *ojitos brillantes*", "*", "*", "D", 15, 5),
    "chew": ("*mastica felizmente*", "^", "^", "w", 6, 0),
    "more": ("¡Quiero más, más, más!", "o", "o", "O", 7, 0),
    "hop": ("¡Qué rico! *da saltitos de alegría*", "^", "^", "v", 9, 0),
    "juicy": ("Mmm... ¡qué jugosita!", "-", "-", "~", 5, 0),
    "rainbow": ("¡Wow! ¡Sabe a arcoíris!", "*", "*", "O", 10, 0),
    "lick": ("*se relame los bigotitos*", "^", "-", "P", 6, 0),
    "meh": ("Mmm... no está mal.", "-", "-", "_", 2, 0),
    "sour": ("¡Ácido! ...¡pero me gusta!", ">", "<", "o", 3, 0),
    "crunch": ("¡Crunch crunch crunch!", "o", "o", "D", 5, 0),
    "choke": ("*come tan rápido que se atraganta* ¡cof cof!", "x", "x", "o", 1, 0),
    "ugh": ("¡Puaj! No me gusta eso...", ">", "<", "n", -5, 0),
    "spit": ("*escupe la comida* ¡Bleeeh!", "x", "x", "P", -7, 0),
    "sniff": ("*lo huele con desconfianza*... bueno, ok.", "o", "-", ".", 0, 0),
    "healthy": ("Lo comeré... porque es sano.", "-", "-", "_", 1, 2),
    "full": ("¡Estoy llenito! No me cabe nada más...", "@", "@", "=", -1, 0),
    "burp": ("*¡eructo!* ...¡perdón!", "o", "o", "O", 3, 0),
    "power": ("¡Siento mucha energía! ¡Wiii!", "O", "O", "D", 5, 12),
    "nap": ("*bosteza y cabecea después de comer* zzz", "-", "-", ".", 4, 8),
}
FRUIT_POOL = ["yum", "chew", "more", "hop", "juicy", "rainbow", "lick",
              "sour", "choke", "burp", "power", "nap"]
VEG_POOL = ["meh", "crunch", "ugh", "spit", "sniff", "healthy", "power", "choke"]

PET_PHRASES = ["se acurruca en tu mano", "cierra los ojitos de felicidad",
               "pide más cariñitos", "se pone panza arriba", "te da un besito"]
BALL_PHRASES = ["¡{n} corre tras la pelota!", "¡{n} atrapó la pelota! ¡Qué hábil!",
                "¡{n} rebota junto con la pelota!", "¡Boing, boing! {n} no se cansa de jugar."]

TITLE_LETTERS = {
    "T": [" _____ ", "|_   _|", "  | |  ", "  |_|  "],
    "E": [" ___ ", "| __|", "| _| ", "|___|"],
    "R": [" ___ ", "| _ \\", "|   /", "|_|_\\"],
    "M": [" __  __ ", "|  \\/  |", "| |\\/| |", "|_|  |_|"],
    "I": [" ___ ", "|_ _|", " | | ", "|___|"],
    "N": [" _  _ ", "| \\| |", "| .` |", "|_|\\_|"],
    "O": ["  ___  ", " / _ \\ ", "| (_) |", " \\___/ "],
    "C": ["  ___ ", " / __|", "| (__ ", " \\___|"],
    "H": [" _  _ ", "| || |", "| __ |", "|_||_|"],
}
TITLE = ["".join(TITLE_LETTERS[ch][i] for ch in "TERMINOCHI") for i in range(4)]

# --------------------------------------------------------------------------
# Musica
# --------------------------------------------------------------------------

class MciBackend:
    """Reproductor nativo de Windows (winmm / MCI), sin dependencias."""
    exts = (".mp3", ".wav", ".wma", ".m4a")
    ALIAS = "terminochi_song"

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._mci = ctypes.windll.winmm.mciSendStringW
        self._mci.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
        self._mci.restype = wintypes.DWORD
        self._buf = ctypes.create_unicode_buffer(128)
        self._open = False

    def _cmd(self, command):
        err = self._mci(command, self._buf, 127, None)
        return err, self._buf.value

    def load(self, path):
        self.unload()
        err, _ = self._cmd(f'open "{path}" type mpegvideo alias {self.ALIAS}')
        self._open = err == 0
        if self._open:
            self._cmd(f"set {self.ALIAS} time format milliseconds")
        return self._open

    def play(self):
        self._cmd(f"play {self.ALIAS}")

    def pause(self):
        self._cmd(f"pause {self.ALIAS}")

    def resume(self):
        err, _ = self._cmd(f"resume {self.ALIAS}")
        if err:
            self._cmd(f"play {self.ALIAS}")

    def unload(self):
        if self._open:
            self._cmd(f"close {self.ALIAS}")
            self._open = False

    def is_finished(self):
        if not self._open:
            return True
        err, mode = self._cmd(f"status {self.ALIAS} mode")
        return err != 0 or mode == "stopped"

    def progress(self):
        """(posicion_ms, duracion_ms) o None."""
        if not self._open:
            return None
        e1, pos = self._cmd(f"status {self.ALIAS} position")
        e2, length = self._cmd(f"status {self.ALIAS} length")
        if e1 or e2 or not pos.isdigit() or not length.isdigit():
            return None
        return int(pos), int(length)


class PygameBackend:
    """Respaldo para Linux / macOS (requiere: pip install pygame)."""
    exts = (".mp3", ".ogg", ".wav", ".flac")

    def __init__(self):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        pygame.mixer.init()
        self._music = pygame.mixer.music

    def load(self, path):
        try:
            self._music.load(path)
            return True
        except Exception:
            return False

    def play(self):
        self._music.play()

    def pause(self):
        self._music.pause()

    def resume(self):
        self._music.unpause()

    def unload(self):
        self._music.stop()

    def is_finished(self):
        return not self._music.get_busy()

    def progress(self):
        pos = self._music.get_pos()
        return (pos, 0) if pos >= 0 else None


def make_backend():
    try:
        return MciBackend() if IS_WINDOWS else PygameBackend()
    except Exception:
        return None


class MusicPlayer:
    def __init__(self):
        self.backend = make_backend()
        self.songs = []
        self.names = []  # nombres sin extension, en el mismo orden que songs
        self.index = 0
        self.state = "stopped"  # stopped | playing | paused
        self._next_check = 0.0
        self._progress = None
        self._next_progress = 0.0
        self.scan()

    def scan(self):
        os.makedirs(MUSIC_DIR, exist_ok=True)
        exts = self.backend.exts if self.backend else (".mp3", ".wav")
        current = self.songs[self.index] if self.songs else None
        files = sorted((f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(exts)), key=str.lower)
        self.songs = [os.path.join(MUSIC_DIR, f) for f in files]
        self.names = [os.path.splitext(f)[0] for f in files]
        if current in self.songs:
            self.index = self.songs.index(current)
        else:
            self.index = min(self.index, max(0, len(self.songs) - 1))

    @property
    def playing(self):
        return self.state == "playing"

    def name(self):
        return self.names[self.index] if self.names else ""

    def play_index(self, i, step=1):
        if not self.backend or not self.songs:
            return False
        n = len(self.songs)
        for k in range(n):
            j = (i + k * step) % n
            if self.backend.load(self.songs[j]):
                self.backend.play()
                self.index = j
                self.state = "playing"
                self._next_check = time.monotonic() + 1.0
                self._next_progress = 0.0
                return True
        self.state = "stopped"
        return False

    def toggle(self):
        if self.state == "playing":
            self.backend.pause()
            self.state = "paused"
        elif self.state == "paused":
            self.backend.resume()
            self.state = "playing"
        else:
            self.play_index(self.index)

    def next(self):
        self.play_index(self.index + 1)

    def prev(self):
        self.play_index(self.index - 1, step=-1)

    def update(self, now):
        if self.state == "playing" and now >= self._next_check:
            self._next_check = now + 0.5
            if self.backend.is_finished():
                self.next()

    def progress(self, now):
        if self.state == "stopped" or not self.backend:
            return None
        if now >= self._next_progress:
            self._next_progress = now + 0.5
            self._progress = self.backend.progress()
        return self._progress

    def close(self):
        if self.backend:
            self.backend.unload()


# --------------------------------------------------------------------------
# Guardado
# --------------------------------------------------------------------------

def load_save():
    try:
        with open(SAVE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("species") in PETS and data.get("name"):
            data.setdefault("place", "jardin")
            for key in ("food", "happiness", "energy"):
                data[key] = float(data.get(key, 70))
            data.setdefault("play_seconds", 0.0)
            data.setdefault("favorite", random.choice([f[0] for f in FOODS if f[1] == "fruta"]))
            data.setdefault("hated", random.choice([f[0] for f in FOODS if f[1] == "verdura"]))
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def write_save(data):
    data = {k: round(v, 2) if isinstance(v, float) else v for k, v in data.items()}
    tmp = SAVE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SAVE_FILE)


def new_save(species, name):
    return {
        "species": species,
        "name": name,
        "place": "jardin",
        "food": 70.0,
        "happiness": 80.0,
        "energy": 80.0,
        "play_seconds": 0.0,
        "favorite": random.choice([f[0] for f in FOODS if f[1] == "fruta"]),
        "hated": random.choice([f[0] for f in FOODS if f[1] == "verdura"]),
        "created": time.strftime("%Y-%m-%d %H:%M"),
    }


# --------------------------------------------------------------------------
# Pantallas de inicio: elegir mascota y nombre
# --------------------------------------------------------------------------

def title_lines():
    lines = [""]
    indent = " " * ((SCENE_W + 2 - len(TITLE[0])) // 2)
    for i, row in enumerate(TITLE):
        lines.append(indent + fg([219, 213, 177, 141][i]) + BOLD + row + RESET)
    return lines


def idle_sprite(species, eyes, now, cheeks=" "):
    pet = PETS[species]
    if now % 3 < 0.2:
        eyes = "-"
    lines, _ = compose_sprite(species, "stand", eyes, eyes, pet["mouth"], cheeks, int(now * 2) % 2)
    return lines


def choose_species(keys):
    sel = 0
    col_w = 34
    while True:
        now = time.monotonic()
        lines = title_lines()
        lines += ["", "   " + COL["text"] + "¡Bienvenido! Elige a tu mascota bebé:" + RESET, ""]
        sprites = []
        for i, key in enumerate(PET_ORDER):
            spr = idle_sprite(key, "^" if i == sel else "o", now + i, "*" if i == sel else " ")
            sprites.append([""] * (10 - len(spr)) + list(spr))
        for row in range(10):
            line = ""
            for i, key in enumerate(PET_ORDER):
                color = fg(PETS[key]["color"]) + BOLD if i == sel else COL["dim"]
                cell = (" " * 9 + sprites[i][row]).ljust(col_w)
                if i == sel:
                    cell = cell.replace("*", COL["blush"] + "*" + color)
                line += color + cell + RESET
            lines.append(line)
        lines.append("")
        label_line = ""
        for i, key in enumerate(PET_ORDER):
            label = PETS[key]["label"]
            if i == sel:
                text = f" ▶ {label} ◀ "
                label_line += " " * 9 + INVERSE + fg(PETS[key]["color"]) + text + RESET + " " * (col_w - 9 - len(text))
            else:
                label_line += COL["dim"] + (" " * 12 + label).ljust(col_w) + RESET
        lines.append(label_line)
        lines += ["", "   " + COL["dim"] + "←/→ elegir   ·   Enter confirmar" + RESET]
        draw(lines)

        key = keys.get_key(0.1)
        if key == "LEFT":
            sel = (sel - 1) % len(PET_ORDER)
        elif key == "RIGHT":
            sel = (sel + 1) % len(PET_ORDER)
        elif key == "ENTER":
            return PET_ORDER[sel]


def choose_name(keys, species):
    name = ""
    pet = PETS[species]
    while True:
        now = time.monotonic()
        lines = title_lines()
        lines += ["", "   " + COL["text"] + f"¿Cómo se llamará tu {pet['label'].lower()}?" + RESET, ""]
        for row in idle_sprite(species, "^", now):
            lines.append(" " * 12 + fg(pet["color"]) + BOLD + row + RESET)
        cursor = "_" if now % 1 < 0.5 else " "
        lines += ["", "   Nombre: " + INVERSE + f" {name}{cursor}".ljust(14) + RESET, ""]
        lines.append("   " + COL["dim"] + "Escribe el nombre y presiona Enter   ·   Esc para volver" + RESET)
        draw(lines)

        key = keys.get_key(0.1)
        if key == "ENTER" and name.strip():
            return name.strip()
        if key == "ESC":
            return None
        if key == "BACKSPACE":
            name = name[:-1]
        elif key and len(key) == 1 and key.isprintable() and len(name) < 12:
            name += key


# --------------------------------------------------------------------------
# Juego principal
# --------------------------------------------------------------------------

MAIN_BUTTONS = ["Comer", "Acariciar", "Pelota", "Lugar", "Música", "Salir"]
BUTTON_HELP = {
    "Comer": "Elige una fruta o verdura para darle de comer.",
    "Acariciar": "Dale cariñitos: sube su felicidad.",
    "Pelota": "Jueguen con la pelota (sube la felicidad, gasta energía).",
    "Lugar": "Cambia entre el jardín (divertido) y la habitación (descanso).",
    "Música": "Abre el reproductor: con música tu mascota baila.",
    "Salir": "Guarda la partida y cierra el juego.",
}
MUSIC_HELP = ["Canción anterior", "Reproducir / pausar", "Canción siguiente", "Volver al menú"]
INNER = SCENE_W
MIN_COLS, MIN_ROWS = INNER + 2, 29
AUTOSAVE_SECONDS = 20  # las acciones y la salida guardan al instante
SIDE = COL["border"] + "║" + RESET
HR_MID = COL["border"] + "╠" + "═" * INNER + "╣" + RESET
STATS_W = 40
BOX_W = INNER - STATS_W - 2
BOX_LABEL = " ♪ Reproduciendo "
BOX_TOP = (COL["dim"] + "┌─" + RESET + fg(213) + BOX_LABEL + RESET + COL["dim"]
           + "─" * (BOX_W - 3 - len(BOX_LABEL)) + "┐" + RESET)
BOX_BOTTOM = COL["dim"] + "└" + "─" * (BOX_W - 2) + "┘" + RESET


def clamp(v):
    return max(0.0, min(100.0, v))


def mmss(ms):
    s = int(ms // 1000)
    return f"{s // 60:02d}:{s % 60:02d}"


class Game:
    def __init__(self, keys, save):
        self.keys = keys
        self.save = save
        self.pet = PETS[save["species"]]
        self.music = MusicPlayer()
        self.mode = "main"  # main | food | music
        self.sel = 0
        self.food_sel = 0
        self.music_sel = 1
        self.action = None  # dict: kind, start, end, ...
        self.say(f"¡Hola de nuevo, {save['name']}!" if save["play_seconds"] > 1 else
                 f"¡{save['name']} acaba de nacer! Cuídalo mucho.")
        self.running = True
        self.last_song = None
        self.term_size = None

    # ---------- estado ----------

    def say(self, text):
        """Guarda el mensaje ya envuelto en 2 lineas con borde."""
        wrapped = textwrap.wrap(text, INNER - 2)[:2]
        wrapped += [""] * (2 - len(wrapped))
        self.msg_lines = [self.border(" " + COL["text"] + w + RESET) for w in wrapped]

    def start_action(self, kind, duration, **data):
        now = time.monotonic()
        self.action = dict(kind=kind, start=now, end=now + duration, **data)

    def tick(self, dt, now):
        s = self.save
        s["play_seconds"] += dt
        s["food"] -= dt / 8
        sad = dt / 10 * (2 if s["food"] < 25 else 1)
        if s["place"] == "jardin":
            sad -= dt / 30
            s["energy"] -= dt / 12
        else:
            s["energy"] += dt / 5
        if self.music.playing:
            s["happiness"] += dt / 3
            s["energy"] -= dt / 8
        s["happiness"] -= sad
        for k in ("food", "happiness", "energy"):
            s[k] = clamp(s[k])
        if self.action and now >= self.action["end"]:
            self.action = None
        if self.music.name() != self.last_song and self.music.state != "stopped":
            self.last_song = self.music.name()
            self.say(f"♪ Ahora suena «{self.last_song}» y {s['name']} empieza a bailar.")

    def save_game(self):
        try:
            write_save(self.save)
        except OSError:
            self.say("No se pudo guardar la partida :(")

    # ---------- acciones ----------

    def feed(self, food):
        name, kind, color, gain = food
        s = self.save
        if s["food"] >= 95:
            rid = "full"
        elif name == s["favorite"]:
            rid = "fav"
        elif name == s["hated"]:
            rid = random.choice(["ugh", "spit"])
        else:
            rid = random.choice(FRUIT_POOL if kind == "fruta" else VEG_POOL)
        text, l, r, m, hap, en = REACTIONS[rid]
        if rid != "full":
            s["food"] = clamp(s["food"] + gain)
        s["happiness"] = clamp(s["happiness"] + hap)
        s["energy"] = clamp(s["energy"] + en)
        self.start_action("eat", 2.6, color=color, eyes=(l, r), mouth=m, hop=rid in ("fav", "hop", "power"))
        self.say(f"{s['name']} come {name.lower()}: {text}")
        self.save_game()

    def caress(self):
        s = self.save
        s["happiness"] = clamp(s["happiness"] + 10)
        self.start_action("pet", 2.8)
        self.say(f"{s['name']} {random.choice(PET_PHRASES)}... {self.pet['sound']}")
        self.save_game()

    def play_ball(self):
        s = self.save
        if s["energy"] < 15:
            self.say(f"{s['name']} está muy cansadito para jugar... llévalo a la habitación a descansar.")
            return
        s["happiness"] = clamp(s["happiness"] + 15)
        s["energy"] = clamp(s["energy"] - 12)
        s["food"] = clamp(s["food"] - 5)
        self.start_action("ball", 4.0)
        self.say(random.choice(BALL_PHRASES).format(n=s["name"]))
        self.save_game()

    def switch_place(self):
        s = self.save
        s["place"] = "habitacion" if s["place"] == "jardin" else "jardin"
        if s["place"] == "jardin":
            self.say(f"{s['name']} sale al jardín a tomar el sol. ¡Aquí se divierte más!")
        else:
            self.say(f"{s['name']} entra a su habitación. Aquí recupera energía.")
        self.save_game()

    # ---------- teclado ----------

    def handle_key(self, key):
        if self.mode == "main":
            if key == "LEFT":
                self.sel = (self.sel - 1) % len(MAIN_BUTTONS)
            elif key == "RIGHT":
                self.sel = (self.sel + 1) % len(MAIN_BUTTONS)
            elif key == "ENTER":
                choice = MAIN_BUTTONS[self.sel]
                if choice == "Comer":
                    self.mode = "food"
                elif choice == "Acariciar":
                    self.caress()
                elif choice == "Pelota":
                    self.play_ball()
                elif choice == "Lugar":
                    self.switch_place()
                elif choice == "Música":
                    self.music.scan()
                    self.mode = "music"
                    if not self.music.backend:
                        self.say("No hay reproductor de audio disponible (en Linux/macOS: pip install pygame).")
                    elif not self.music.songs:
                        self.say("La carpeta 'music' está vacía. Pon ahí tus canciones (.mp3, .wav...).")
                elif choice == "Salir":
                    self.running = False
        elif self.mode == "food":
            total = len(FOOD_LABELS)
            if key == "LEFT":
                self.food_sel = (self.food_sel - 1) % total
            elif key == "RIGHT":
                self.food_sel = (self.food_sel + 1) % total
            elif key == "ESC":
                self.mode = "main"
            elif key == "ENTER":
                if self.food_sel == len(FOODS):
                    self.mode = "main"
                else:
                    self.feed(FOODS[self.food_sel])
        elif self.mode == "music":
            if key == "LEFT":
                self.music_sel = (self.music_sel - 1) % 4
            elif key == "RIGHT":
                self.music_sel = (self.music_sel + 1) % 4
            elif key == "ESC":
                self.mode = "main"
            elif key == "ENTER":
                if self.music_sel == 3:
                    self.mode = "main"
                    return
                if not self.music.backend or not self.music.songs:
                    self.music.scan()
                    if not self.music.songs:
                        self.say("No hay canciones. Pon archivos .mp3 o .wav en la carpeta 'music'.")
                        return
                if self.music_sel == 0:
                    self.music.prev()
                elif self.music_sel == 1:
                    self.music.toggle()
                    if self.music.state == "paused":
                        self.say(f"Música en pausa. {self.save['name']} se queda esperando...")
                elif self.music_sel == 2:
                    self.music.next()
                if self.music.state == "stopped":
                    self.say("No se pudo reproducir ninguna canción de la carpeta 'music'.")

    # ---------- dibujo de la mascota ----------

    def mood_face(self, now):
        s = self.save
        m = self.pet["mouth"]
        if s["energy"] < 15:
            return "-", "-", ".", " "
        if s["food"] < 20:
            return "T", "T", "n", " "
        if s["happiness"] < 30:
            l, r, m = ".", ".", "n"
        elif s["happiness"] > 70:
            l, r = "^", "^"
        else:
            l, r = "o", "o"
        if now % 3.7 < 0.18:
            l, r = "-", "-"
        return l, r, m, "*" if s["happiness"] > 85 else " "

    def mood_label(self, now):
        s = self.save
        if self.music.playing:
            return "Bailando: " + DANCES[int(now * 4) // DANCE_STEPS % len(DANCES)][0]
        if s["energy"] < 15:
            return "Dormido zZz"
        if s["food"] < 20:
            return "Con hambre"
        if s["happiness"] < 30:
            return "Triste"
        return "Feliz" if s["happiness"] > 70 else "Tranquilo"

    def pet_pose(self, now):
        """Devuelve (lineas, x, fila_superior, detras, delante): detras y delante son
        listas de (x, y, texto, color) dibujadas antes y despues de la mascota."""
        species = self.save["species"]
        pet = self.pet
        cx = SCENE_W // 2
        x = cx - 7
        pose, dy, head_dx, back = "stand", 0, 0, False
        tail = int(now * 2) % 2
        behind, extras = [], []
        a = self.action

        if a and a["kind"] == "eat":
            t = now - a["start"]
            l, r = a["eyes"]
            m = a["mouth"] if int(t * 5) % 2 == 0 or t > 1.8 else "o"
            c = "*" if a["hop"] else " "
            if a["hop"] and t > 1.8:
                dy = int(abs(math.sin(t * 8)) * 2)
                pose = "up"
            bite = ["(@)", "(@", "(", ""][min(3, int(t / 0.6))]
            if bite:
                top = FLOOR_Y + 1 - pet["head_h"] - len(BODIES[pose])
                extras.append((x + 15, top + pet["mouth_row"] - 1, " ,", COL["leaf"]))
                extras.append((x + 15, top + pet["mouth_row"], bite, fg(a["color"])))
        elif a and a["kind"] == "pet":
            t = now - a["start"]
            l, r, m, c = "^", "^", pet["mouth"], "*"
            head_dx = int(round(math.sin(t * 3)))
            tail = int(t * 6) % 2
            top = FLOOR_Y + 1 - pet["head_h"] - len(BODIES[pose])
            # una manita que acaricia la cabeza
            extras.append((x + 4 + int(round(math.sin(t * 5) * 3)), top - 1, "\\_\\_\\", COL["text"]))
            for i, hx in enumerate((-4, 2, 8, 14, 19)):
                phase = (t * 0.9 + i / 5) % 1
                extras.append((x + hx + int(round(math.sin(phase * 6 + i))), top - 1 - int(phase * 5),
                               "♥", fg([204, 211, 198, 213, 205][i])))
        elif a and a["kind"] == "ball":
            t = now - a["start"]
            bx = cx + int(34 * math.sin(t * 1.6))
            by = FLOOR_Y - int(abs(math.sin(t * 4.5)) * 6)
            x = cx - 7 + int(24 * math.sin(t * 1.6 - 0.6))
            x = max(0, min(SCENE_W - PET_W, x))
            pose = "walk1" if int(t * 7) % 2 else "walk2"
            dy = int(abs(math.sin(t * 7)) * 1.5)
            head_dx = 1 if bx > x + 7 else -1
            l, r, m, c = "O", "O", "D", "*"
            extras.append((max(0, min(SCENE_W - 1, bx)), by, "O", COL["ball"]))
            if by < FLOOR_Y:
                behind.append((max(0, min(SCENE_W - 1, bx)), FLOOR_Y, ".", COL["shadow"]))
        elif self.music.playing:
            step = int(now * 4)
            name, moves = DANCES[step // DANCE_STEPS % len(DANCES)]
            pose, dx, dy, head_dx, back = moves[step % DANCE_STEPS % len(moves)]
            x += dx
            l, r, m = DANCE_FACES[(step // 2) % len(DANCE_FACES)]
            c = "*"
            tail = step % 2
            extras += self.dance_extras(now, step, x, dy)
        else:
            l, r, m, c = self.mood_face(now)
            if self.save["energy"] < 15:
                pose = "squat"
                top = FLOOR_Y + 1 - pet["head_h"] - len(BODIES[pose])
                phase = int(now * 1.5) % 4
                for i in range(min(phase, 3)):
                    extras.append((x + 15 + i * 2, top - i, "zZz"[i], COL["zzz"]))
            elif species == "pollito" and now % 6 < 0.6:  # aleteo de vez en cuando
                pose = "up" if int(now * 8) % 2 else "stand"
            elif now % 8 < 1.2:  # inclina la cabecita
                head_dx = 1

        lines, ox = compose_sprite(species, pose, l, r, m, c, tail, head_dx, bool(back))
        top = FLOOR_Y + 1 - len(lines) - dy
        if dy > 0:  # sombra en el suelo mientras salta
            w = max(3, 11 - dy * 2)
            behind.append((x + 7 - w // 2, FLOOR_Y, "." * w, COL["shadow"]))
        return lines, x + ox, top, behind, extras

    def dance_extras(self, now, step, x, dy):
        extras = []
        top = FLOOR_Y - 9 - dy
        for i in range(6):  # notas que suben flotando
            phase = (now * 0.5 + i / 6) % 1
            nx = x + 7 + int(round(math.sin(phase * 7 + i * 1.9) * (10 + i)))
            ny = int(round(top + 2 - phase * (top + 2)))
            extras.append((nx, ny, "♪" if i % 2 else "♫", fg(PARTY[(i + step // 2) % len(PARTY)])))
        rng = random.Random(step)
        for _ in range(4):  # destellos alrededor
            extras.append((x + rng.randint(-9, 23), rng.randint(top, FLOOR_Y - 1),
                           rng.choice("*+·"), fg(rng.choice(PARTY))))
        return extras

    # ---------- dibujo del escenario ----------

    def scene_extras(self, grid, now):
        """Animaciones del lugar (nubes, sol, flores, estrellas, reloj, fiesta)."""
        dancing = self.music.playing and not self.action
        step = int(now * 4)
        if self.save["place"] == "jardin":
            sun = SUN[step % 2 if dancing else int(now / 1.5) % 2]
            face = ("^o^" if step % 2 else ">v<") if dancing else "^ ^"
            stamp(grid, [s.format(f=face) for s in sun], 2, 0, COL["sun"])
            for art, y, speed, offset in CLOUDS:
                w = max(map(len, art))
                cx = int((offset + now * speed) % (SCENE_W + w)) - w
                stamp(grid, art, cx, y, COL["cloud"])
            for i, (fx, head, color) in enumerate(FLOWERS):
                if dancing:
                    sway = 1 if (step + i) % 2 else -1
                    leaves = "\\|/" if (step + i) % 2 else "-|-"
                else:
                    sway = int(round(math.sin(now * 1.2 + i) * 0.6))
                    leaves = "\\|/"
                stamp(grid, [head], fx + 1 + sway, FLOOR_Y - 2, fg(color))
                stamp(grid, [leaves, " |"], fx, FLOOR_Y - 1, COL["stem"])
            # mariposas: una paseando, o dos bailando alrededor de la mascota
            wings = "\\o/" if int(now * 6) % 2 else "/o\\"
            if dancing:
                for k in (0, math.pi):
                    ang = now * 2 + k
                    stamp(grid, [wings], SCENE_W // 2 - 1 + int(16 * math.cos(ang)),
                          5 + int(2 * math.sin(ang)), COL["butterfly"])
            else:
                stamp(grid, [wings], 50 + int(40 * math.sin(now * 0.21)),
                      6 + int(round(2 * math.sin(now * 1.3))), COL["butterfly"])
        else:
            for i, (sx, sy) in enumerate(STARS):
                ch = "*+.·"[(int(now * 2) + i * 3) % 4] if not dancing else "*+"[(step + i) % 2]
                grid[WINDOW_Y + sy][WINDOW_X + sx] = (ch, COL["star"])
            hx, hy, hand = CLOCK_HANDS[int(now) % 8]
            stamp(grid, [hand], CLOCK_X + 5 + hx, CLOCK_Y + 2 + hy, fg(203))
            if dancing:
                ball = DISCO[step % 2]
                c1, c2 = fg(PARTY[step % 8]), fg(PARTY[(step + 4) % 8])
                stamp(grid, ball, SCENE_W // 2 - 2, 0, fg(250), {"#": c1, ":": c2})
                rng = random.Random(step)
                for _ in range(16):  # luces de la bola disco en paredes
                    r, c = rng.randrange(FLOOR_Y), rng.randrange(SCENE_W)
                    if grid[r][c][1] in ("", COL["wall"]):
                        grid[r][c] = (rng.choice("*+·"), fg(rng.choice(PARTY)))
                for i in range(0, SCENE_W, 5):  # pista de baile
                    stamp(grid, ["▓▓▓▓▓"], i, FLOOR_Y + 2, fg(PARTY[(i // 5 + step) % 8]))

    def scene_lines(self, now):
        grid = [row[:] for row in SCENES[self.save["place"]]]
        self.scene_extras(grid, now)
        lines, x, top, behind, extras = self.pet_pose(now)
        for ex, ey, text, color in behind:
            stamp(grid, [text], ex, ey, color)
        stamp(grid, lines, x, top, fg(self.pet["color"]) + BOLD, {"*": COL["blush"]})
        for ex, ey, text, color in extras:
            stamp(grid, [text], ex, ey, color)
        return grid_to_lines(grid)

    # ---------- dibujo de la interfaz ----------

    def border(self, text):
        return SIDE + pad(text, INNER) + SIDE

    def stats_lines(self):
        out = []
        for label, key in (("Comida", "food"), ("Felicidad", "happiness"), ("Energía", "energy")):
            v = self.save[key]
            filled = int(round(v / 100 * 20))
            color = fg(77) if v > 60 else fg(220) if v > 30 else fg(203)
            bar = color + "█" * filled + COL["dim"] + "░" * (20 - filled) + RESET
            text = f" {COL['text']}{label:<10}{RESET}{bar} {COL['text']}{int(v):3d}%{RESET}"
            out.append(text + " " * (STATS_W - 36))
        return out

    def music_box(self, now):
        m = self.music
        w = BOX_W - 4
        if m.songs:
            icon = {"playing": "▶ ", "paused": "❚❚", "stopped": "■ "}[m.state]
            counter = f"[{m.index + 1}/{len(m.songs)}]"
            space = w - 2 - 1 - 1 - len(counter)
            name = m.name()
            if len(name) > space:  # marquesina para nombres largos
                loop = name + "   ·   "
                start = int(now * 4) % len(loop)
                name = (loop * 2)[start:start + space]
            color = fg(156) if m.playing else COL["text"]
            body = f"{fg(213)}{icon}{RESET} {color}{name.ljust(space)}{RESET} {COL['dim']}{counter}{RESET}"
        else:
            body = COL["dim"] + "Sin canciones · pon tus audios en la carpeta 'music'".ljust(w) + RESET
        middle = COL["dim"] + "│ " + RESET + body + COL["dim"] + " │" + RESET

        bottom = BOX_BOTTOM
        prog = m.progress(now)
        if prog:
            pos, length = prog
            if length > 0:
                n = BOX_W - 2 - 2 - 14
                done = min(n, int(n * pos / length))
                bar = fg(213) + "━" * done + "●" + COL["dim"] + "─" * max(0, n - done - 1)
                bottom = (COL["dim"] + "└─ " + COL["text"] + mmss(pos) + " " + bar + " "
                          + COL["text"] + mmss(length) + COL["dim"] + " ─┘" + RESET)
            else:
                bottom = (COL["dim"] + "└─ " + COL["text"] + mmss(pos) + COL["dim"] + " "
                          + "─" * (BOX_W - 11) + "┘" + RESET)
        return [BOX_TOP, middle, bottom]

    def buttons_row(self, labels, selected):
        out = []
        for i, label in enumerate(labels):
            if i == selected:
                out.append(bg(219) + fg(16) + BOLD + f" ▸ {label}  " + RESET)
            else:
                out.append(COL["text"] + f"   {label}  " + RESET)
        row = "  ".join(out)
        return " " * max(1, (INNER - vis_len(row)) // 2) + row

    def controls(self):
        if self.mode == "main":
            desc = BUTTON_HELP[MAIN_BUTTONS[self.sel]]
            return [self.buttons_row(MAIN_BUTTONS, self.sel), "  " + COL["dim"] + desc + RESET], \
                "←/→ moverse · Enter seleccionar"
        if self.mode == "music":
            play = "❚❚ Pausa" if self.music.playing else "▶ Play"
            labels = ["|◀◀ Anterior", play, "▶▶| Siguiente", "Volver"]
            return [self.buttons_row(labels, self.music_sel), "  " + COL["dim"] + MUSIC_HELP[self.music_sel] + RESET], \
                "←/→ moverse · Enter seleccionar · Esc volver"
        # comida: carrusel
        n = len(FOOD_LABELS)
        lo = max(0, min(self.food_sel - 2, n - 6))
        row = self.buttons_row(FOOD_LABELS[lo:lo + 6], self.food_sel - lo)
        if self.food_sel < len(FOODS):
            name, kind, color, gain = FOODS[self.food_sel]
            info = (f"  {fg(color)}●{RESET} {COL['text']}{name}{RESET} {COL['dim']}· {kind} · llena +{gain}"
                    f"   ({self.food_sel + 1}/{n}){RESET}")
        else:
            info = f"  {COL['dim']}Volver al menú{RESET}"
        arrows = COL["dim"] + ("◀" if lo > 0 else " ") + RESET
        more = COL["dim"] + ("▶" if lo + 6 < n else " ") + RESET
        return [info, " " + arrows + row[2:] + " " + more], \
            "←/→ elegir comida · Enter dar de comer · Esc volver"

    def size_warning(self, cols, rows):
        draw(["", "  " + COL["title"] + "TERMINOCHI" + RESET, "",
              f"  La ventana es muy pequeña ({cols}x{rows}). Necesito al menos {MIN_COLS}x{MIN_ROWS}.",
              "  Maximiza la ventana o reduce el zoom con Ctrl y la tecla - (menos).",
              "", "  " + COL["dim"] + f"{self.save['name']} te espera..." + RESET])

    def render(self, now):
        size = shutil.get_terminal_size((MIN_COLS, MIN_ROWS))
        if size != self.term_size:
            self.term_size = size
            clear_screen()
        if size.columns < MIN_COLS or size.lines < MIN_ROWS:
            self.size_warning(size.columns, size.lines)
            return

        s = self.save
        mins = int(s["play_seconds"] // 60)
        age = f"{mins // 60} h {mins % 60:02d} min" if mins >= 60 else f"{mins} min"
        title = f" TERMINOCHI ═ {s['name']} el {self.pet['label'].lower()} ═ {PLACES[s['place']]} ═ Edad: {age} "
        mood = f" {self.mood_label(now)} "
        fill = max(0, INNER - 2 - len(title) - len(mood) - 2)
        top = (COL["border"] + "╔══" + COL["title"] + title + RESET + COL["border"] + "═" * fill
               + fg(156) + mood + COL["border"] + "══╗" + RESET)

        lines = [top]
        # las filas del escenario ya miden exactamente SCENE_W: no necesitan pad()
        lines += [SIDE + l + SIDE for l in self.scene_lines(now)]
        lines.append(HR_MID)
        for stat, box in zip(self.stats_lines(), self.music_box(now)):
            lines.append(SIDE + stat + box + "  " + SIDE)
        lines.append(HR_MID)
        lines += self.msg_lines
        lines.append(HR_MID)
        rows, hint = self.controls()
        lines += [self.border(r) for r in rows]
        hint = f" {hint} "
        lines.append(COL["border"] + "╚══" + COL["dim"] + hint + COL["border"]
                     + "═" * (INNER - 2 - len(hint)) + "╝" + RESET)
        draw(lines)

    # ---------- bucle ----------

    def run(self):
        last = last_save = time.monotonic()
        while self.running:
            key = self.keys.get_key(0.08)
            now = time.monotonic()
            if key:
                self.handle_key(key)
            self.tick(min(1.0, now - last), now)
            last = now
            self.music.update(now)
            if now - last_save > AUTOSAVE_SECONDS:
                self.save_game()
                last_save = now
            if self.running:
                self.render(now)
        self.save_game()
        self.music.close()


def main():
    if IS_WINDOWS:
        os.system("")  # activa los codigos ANSI en la consola de Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    os.makedirs(MUSIC_DIR, exist_ok=True)
    sys.stdout.write("\x1b]0;TERMINOCHI\x07\x1b[?25l\x1b[2J")
    game = None
    try:
        with KeyReader() as keys:
            save = load_save()
            while save is None:
                species = choose_species(keys)
                name = choose_name(keys, species)
                if name:
                    save = new_save(species, name)
                    write_save(save)
                clear_screen()
            clear_screen()
            game = Game(keys, save)
            game.run()
    except KeyboardInterrupt:
        if game:
            game.save_game()
            game.music.close()
    finally:
        sys.stdout.write(RESET + "\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()
    if game:
        print(f"¡Adiós! {game.save['name']} te espera para la próxima. (Partida guardada)")


if __name__ == "__main__":
    main()
