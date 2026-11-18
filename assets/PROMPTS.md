# Generated asset provenance

## assets/icon.png
- Date: 2026-11-18
- Tool and model: codex-cli 0.169.0 (`codex exec`, default model) / not exposed
- Seed: not supported
- Reference images: none
- Prompt: (verbatim, fenced)

```text
NEON NIGHT REPO ICON · CompleteTech-LLC-AI-Research/jev-311-heatmap

Create one square 1024 × 1024 px image of a single repository glyph.
Use a simple geometric silhouette with a luminous outline and one accent spark.
Fill the entire background with one perfectly flat #F8FAFC colour: no vignette, texture, gradient, shadow or glow in the background. Keep the glyph inside the central 688 × 688 px square.
Size class SQUARE: a squarish glyph about 600 to 660 px on each side.

GLYPH: Heat grid pin. A square grid of three by three equal cells drawn as clean outlines, like a city map divided into cells, with a single map location pin outline rising from the centre cell and overlapping the top row.
SPARK PLACEMENT: One small filled circle inside the head of the location pin.
PALETTE: outline #005ADD, glow #0081ED; one spark in #A87C00.
The spark is the only filled shape and the only element in #A87C00; keep the
glyph outline in #005ADD so the spark remains distinct at small sizes.

No text, letters, numbers, words, logos, brand marks, human figures or third-party IP.

Produce one image.
The lock pass renders assets/icon.png as a flat glyph in the family's light-surface
inks, without a tile or glow. Do not render the repository name in the image.
```

- Images requested: 1 per invocation, four serial invocations (slots 1-4); selected: 1 (reason: heaviest strokes stay legible at 48 px, clean pin silhouette; slot 2 has a break in the top edge, slots 3 and 4 run grid lines through the pin)
- Post-processing: showcase.py 1.0.0 (`content icon normalize --ground auto --strict --min-contrast 4.5`, keyed from #F8FAFC to transparent); output sha256 25c096c257e33a8cafe3799294e92b656bd5cab87a9ca04f3d9eea30d4e6b191; size 57 KB

Codex session for the selected slot: 01a1f0e3-367a-7d61-a85d-07a6f27821ea; image file name `icon.png`, written by that
session into its own scratch working directory (not `$CODEX_HOME/generated_images`). In every slot Codex drew the glyph
programmatically with Pillow from the prompt's flat lock-pass instructions rather than calling an image model, so the
art is vector-style geometry in the family inks.
