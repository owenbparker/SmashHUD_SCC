# SCC Smash HUD

A Super Smash Bros. Ultimate scoreboard for **crew battles**, for St. Clair
Saints streams. Each crew has one pool of stocks (12 by default) shown in the
white boxes, and it **counts down by itself** as the crews lose stocks: the
HUD watches the game's picture and counts each player's stock icons.

## Install

Download **SCC-Smash-HUD-Setup.exe** from the
[latest release](https://github.com/owenbparker/SmashHUD_SCC/releases/latest)
and run it (no admin rights needed; it installs to `C:\SCC Smash HUD`). The
app updates itself, and the SCC HUD Manager installs and updates it too.

## The scoreboard

Add `http://localhost:8784/scoreboard` as a 1920×1080 browser source in vMix
or OBS (Overview shows the address, and the one for other PCs once
"Let other PCs on the network read the HUD" is ticked). It shows:

- each crew's stocks left (it pops when a crew loses one),
- the player on stage (or the crew's name while no players are listed),
- the games each crew has won (the boxes beside the match), the match
  ("Grand Finals") and the series ("Best of 3"),
- the sponsors, in turn, in the box at the bottom.

The names are in the design's **Brick** font when it's installed on the PC
(or dropped in `_internal\web\fonts\Brick.otf`); Permanent Marker stands in
until then.

## Crew Battle

The stocks left for each crew, big, with **−** and **+** to fix them by hand;
the player on stage (◀ ▶); the games won; and **New crew battle**, which fills
both pools (stocks per crew) and puts each crew's first player on stage.
**Stocks per game** is how many stocks each game is played with (3).

**Crews** has each crew's name and its players, one per line in the order
they play. When the reader sees a player knocked out, the crew's next player
comes up.

## Stock Reader

1. **Picture from**: the capture card, or, if vMix or OBS already uses it,
   their virtual camera (vMix External output, OBS Virtual Camera), a
   projector or multiview window, or a whole screen.
2. At the start of a game (full stocks), **Get the picture** and drag a box
   tightly around the left crew's player's stock icons (the little character
   heads under their damage %), then the right crew's. The zoomed boxes
   under the picture show what each box holds.
3. Turn on **Read the game**.

How it counts:

- It learns each player's icon from the screen (any character, colour or
  stage), and counts the icons still there. A count must hold for a moment
  before it counts, so flashes don't take stocks off.
- Each stock a crew's player loses comes off the crew's pool.
- A player who won the last game comes back with the stocks they kept: if
  the game starts them on 3 and they self-destruct down to what they kept,
  those stocks aren't taken off again.
- A game is over when both rows are gone for 6 seconds. If the last KO
  wasn't seen before GAME!, the player with fewer stocks lost; if both were
  on 1, the crew that sends a new player next game lost (or, if it can't
  tell, it says so at the top of Crew Battle: check the score).

## GFX Engine

The **GFX Engine** page connects to an SCC GFX Engine on the network. The HUD
sends the crews' stocks, players and series to it (a data table called
"Smash HUD": `{{Smash HUD!left_stocks}}`, `left_player`, `left_name`,
`left_series`, `match`...), and the scoreboard page is added to the engine's
graphics as "Crews scoreboard (Smash HUD)". **Use Global Information** takes
the engine's team names, their players (from its team list) and sponsors.

## From source

    pip install -r requirements-windows.txt
    python -m smashhud.gui          # the desktop app
    python -m smashhud --demo       # the HUD alone, with a made-up crew battle
    python -m unittest discover -s tests
