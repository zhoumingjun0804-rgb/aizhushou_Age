---
name: gif-to-svga
description: |
  GIF to SVGA format converter. Converts GIF animation files to SVGA animation format, the generated .svga files can be played on iOS, Android, Web, Flutter and other platforms. Trigger: gif to svga, gif2svga, GIF转SVGA, svga转换, convert gif to svga.
---

# GIF to SVGA Converter

Convert GIF animation files to SVGA animation format. The generated .svga files can be played on iOS, Android, Flutter, Web, HarmonyOS and other platforms.

## How It Works

1. Parse GIF file to extract each frame as an image
2. Convert each frame to PNG bitmap
3. Generate SVGA 1.x format `movie.spec` JSON descriptor
4. Package as `.svga` file (ZIP archive)

## Usage

### Command Line

```bash
node <SKILL_DIR>/scripts/gif2svga.js <input.gif> [output.svga] [--fps=20]
```

| Arg | Description |
|-----|-------------|
| `input.gif` | Input GIF file (required) |
| `output.svga` | Output SVGA file (optional, defaults to input with .svga extension) |
| `--fps=N` | Frame rate override (optional, valid: 1,2,3,5,6,10,12,15,20,30,60) |

### Programmatic API

```js
const { gif2svga } = require('./scripts/gif2svga');

const result = await gif2svga('input.gif', 'output.svga', { fps: 20 });
// Returns: { inputPath, outputPath, width, height, totalFrames, fps }
```

## Dependencies

```bash
npm install
```

Requires: `sharp` (image processing), `adm-zip` (ZIP packaging).

## SVGA Format Notes

The generated SVGA file uses the 1.x JSON format:
- `.svga` is essentially a ZIP archive, no subdirectories allowed when extracted
- `movie.spec`: JSON descriptor file defining animation structure
- `*.png`: PNG-8 or PNG-24 bitmap files (one per GIF frame)

Conversion logic:
```
GIF frame sequence -> each frame = one PNG image
                   -> each PNG = one Sprite entity
                   -> Sprite visible only at its own frame index, hidden at others
```

## File Structure

```
gif-to-svga/
├── SKILL.md
├── package.json
└── scripts/
    └── gif2svga.js
```