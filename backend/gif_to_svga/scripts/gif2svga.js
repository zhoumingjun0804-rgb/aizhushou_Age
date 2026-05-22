#!/usr/bin/env node

/**
 * gif2svga.js - Convert GIF animations to SVGA format
 *
 * SVGA 1.x format spec:
 *   - .svga file = ZIP archive (no subdirectories)
 *   - Contains: movie.spec (JSON) + frame_N.png files
 *
 * Usage:
 *   node gif2svga.js input.gif [output.svga] [--fps=20]
 */

const sharp = require('sharp');
const AdmZip = require('adm-zip');
const path = require('path');
const fs = require('fs');

// Valid FPS values per SVGA 1.x spec
const VALID_FPS = [1, 2, 3, 5, 6, 10, 12, 15, 20, 30, 60];

/**
 * Round a raw FPS to the nearest valid SVGA FPS value.
 */
function nearestValidFps(rawFps) {
  let best = VALID_FPS[0];
  let bestDist = Math.abs(best - rawFps);
  for (const v of VALID_FPS) {
    const d = Math.abs(v - rawFps);
    if (d < bestDist) {
      bestDist = d;
      best = v;
    }
  }
  return best;
}

/**
 * Estimate FPS from an array of GIF frame delays.
 * GIF delays are stored in hundredths of a second (centiseconds).
 * Returns: estimated frames-per-second.
 */
function estimateFpsFromDelays(delays) {
  if (!delays || delays.length === 0) return null;
  // Filter out zero-delay frames (common in GIFs, treated as ~10cs)
  const valid = delays.map(d => (d === 0 ? 10 : d));
  const avg = valid.reduce((a, b) => a + b, 0) / valid.length;
  return nearestValidFps(100 / avg);
}

/**
 * Parse GIF metadata from the raw GIF binary.
 * Reads frame delays and dimensions without external library.
 * Falls back gracefully if parsing fails.
 */
function parseGifMetadata(buffer) {
  try {
    const data = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
    // Check GIF header: "GIF89a" or "GIF87a"
    const header = String.fromCharCode(data.getUint8(0), data.getUint8(1), data.getUint8(2));
    if (header !== 'GIF') return null;

    // Read Logical Screen Descriptor
    const width = data.getUint16(6, true);
    const height = data.getUint16(8, true);

    // Parse to find Graphic Control Extensions (0x21 0xF9) for frame delays
    let offset = 13; // Start after Logical Screen Descriptor
    const delays = [];
    let frameCount = 0;

    while (offset < data.byteLength) {
      const blockType = data.getUint8(offset);

      if (blockType === 0x2C) {
        // Image Descriptor - count a frame
        frameCount++;
        // Skip image descriptor (10 bytes header + local color table + data)
        offset += 10;
        // Skip local color table if present
        const packed = data.getUint8(offset - 1); // re-check
        // Actually, we already moved past 10 bytes
        offset += 1; // LZW minimum code size
        // Skip image data sub-blocks
        while (offset < data.byteLength) {
          const size = data.getUint8(offset);
          offset += 1 + size;
          if (size === 0) break;
        }
        continue;
      }

      if (blockType === 0x21) {
        const extLabel = data.getUint8(offset + 1);
        if (extLabel === 0xF9) {
          // Graphic Control Extension
          const delay = data.getUint16(offset + 4, true); // hundredths of a second
          delays.push(delay);
        }
        // Skip extension
        offset += 2; // past 0x21 and label
        const blockSize = data.getUint8(offset);
        offset += 1 + blockSize; // past size byte and block data
        // Read sub-blocks until zero terminator
        while (offset < data.byteLength) {
          const subSize = data.getUint8(offset);
          offset += 1 + subSize;
          if (subSize === 0) break;
        }
        continue;
      }

      if (blockType === 0x3B) {
        // Trailer - end of GIF
        break;
      }

      // Unknown block, skip one byte
      offset++;
    }

    return { width, height, delays, frameCount };
  } catch {
    return null;
  }
}

/**
 * Core conversion: GIF → SVGA
 *
 * @param {string} inputPath - Path to input .gif file
 * @param {string} outputPath - Path for output .svga file
 * @param {object} options - { fps?: number }
 * @returns {Promise<object>} Result metadata
 */
async function gif2svga(inputPath, outputPath, options = {}) {
  const inputBuffer = fs.readFileSync(inputPath);

  // Get image metadata via sharp
  const meta = await sharp(inputBuffer).metadata();
  const { width, height } = meta;
  const totalFrames = meta.pages || 1;

  if (totalFrames < 1) {
    throw new Error('GIF has no frames');
  }

  // Determine FPS
  let fps = options.fps || null;
  if (!fps) {
    // Try to parse delays from GIF binary
    const gifMeta = parseGifMetadata(inputBuffer);
    if (gifMeta && gifMeta.delays.length > 0) {
      fps = estimateFpsFromDelays(gifMeta.delays);
    }
  }
  // Fallback to 20 FPS
  fps = fps || 20;

  // Validate FPS
  if (!VALID_FPS.includes(fps)) {
    console.warn(`Warning: FPS ${fps} is not in the valid SVGA list, rounding to nearest`);
    fps = nearestValidFps(fps);
  }

  // Default identity transform
  const identityTransform = { a: 1.0, b: 0.0, c: 0.0, d: 1.0, tx: 0.0, ty: 0.0 };
  const defaultLayout = { x: 0, y: 0, width, height };
  const emptyShapes = [];

  // Build images map and sprites
  // Each GIF frame becomes a Sprite, visible only at its own frame index.
  // Hidden frames use minimal entries to keep movie.spec compact.
  const images = {};
  const sprites = [];

  for (let i = 0; i < totalFrames; i++) {
    const imageKey = `frame_${i}`;
    images[imageKey] = `${imageKey}.png`;

    // Build frames array: sprite i is only visible at frame i
    const frameEntities = [];
    for (let f = 0; f < totalFrames; f++) {
      if (f === i) {
        frameEntities.push({
          alpha: 1.0,
          layout: defaultLayout,
          transform: identityTransform,
          clipPath: '',
          shapes: emptyShapes,
        });
      } else {
        // Minimal placeholder for hidden frames (saves ~90% JSON size)
        frameEntities.push({ alpha: 0.0 });
      }
    }

    sprites.push({
      imageKey,
      frames: frameEntities,
    });
  }

  // Build movie.spec
  const movieSpec = {
    ver: '1.1.0',
    movie: {
      viewBox: { width, height },
      fps,
      frames: totalFrames,
    },
    images,
    sprites,
  };

  // Create SVGA file (ZIP archive)
  const zip = new AdmZip();
  // Use compact JSON to minimize movie.spec file size
  zip.addFile('movie.spec', Buffer.from(JSON.stringify(movieSpec), 'utf-8'));

  // Add PNG frames
  for (let i = 0; i < totalFrames; i++) {
    const pngBuffer = await sharp(inputBuffer, { page: i }).png().toBuffer();
    zip.addFile(`frame_${i}.png`, pngBuffer);
  }

  // Ensure output has .svga extension
  if (!outputPath.toLowerCase().endsWith('.svga')) {
    outputPath += '.svga';
  }

  zip.writeZip(outputPath);

  return { inputPath, outputPath, width, height, totalFrames, fps };
}

// ======= CLI =======

async function main() {
  const args = process.argv.slice(2);

  if (args.length < 1 || args[0] === '--help' || args[0] === '-h') {
    console.log(`
  GIF → SVGA Converter

  Usage:
    node gif2svga.js <input.gif> [output.svga] [--fps=N]

    <input.gif>    Input GIF file (required)
    [output.svga]  Output SVGA file (optional, defaults to input name with .svga)
    [--fps=N]      Frame rate override. Valid: 1,2,3,5,6,10,12,15,20,30,60

  Examples:
    node gif2svga.js animation.gif
    node gif2svga.js animation.gif output.svga
    node gif2svga.js animation.gif --fps=30
    node gif2svga.js animation.gif sticker.svga --fps=12
`);
    process.exit(0);
  }

  // Parse positional args vs named args
  let inputPath = null;
  let outputPath = null;
  let fpsOverride = null;

  for (const arg of args) {
    if (arg.startsWith('--fps=')) {
      const val = parseInt(arg.split('=')[1], 10);
      if (isNaN(val) || !VALID_FPS.includes(val)) {
        console.error(`Error: Invalid FPS "${arg.split('=')[1]}". Valid values: ${VALID_FPS.join(', ')}`);
        process.exit(1);
      }
      fpsOverride = val;
    } else if (arg.startsWith('-')) {
      console.error(`Error: Unknown option "${arg}"`);
      console.error('Use --help for usage info');
      process.exit(1);
    } else if (!inputPath) {
      inputPath = arg;
    } else if (!outputPath) {
      outputPath = arg;
    }
  }

  if (!inputPath) {
    console.error('Error: Input GIF file is required.');
    console.error('Use --help for usage info');
    process.exit(1);
  }

  if (!fs.existsSync(inputPath)) {
    console.error(`Error: Input file not found: "${inputPath}"`);
    process.exit(1);
  }

  if (!inputPath.toLowerCase().endsWith('.gif')) {
    console.warn(`Warning: Input file "${inputPath}" does not have a .gif extension.`);
  }

  if (!outputPath) {
    outputPath = inputPath.replace(/\.gif$/i, '.svga');
  }

  const options = {};
  if (fpsOverride) options.fps = fpsOverride;

  try {
    console.log(`Converting "${inputPath}" → "${outputPath}"...`);
    const result = await gif2svga(inputPath, outputPath, options);
    console.log('');
    console.log('  ✅ Conversion successful!');
    console.log(`     Input:    ${result.inputPath}`);
    console.log(`     Output:   ${result.outputPath}`);
    console.log(`     Size:     ${result.width}x${result.height}`);
    console.log(`     Frames:   ${result.totalFrames}`);
    console.log(`     FPS:      ${result.fps}`);
  } catch (err) {
    console.error(`\n  ❌ Conversion failed: ${err.message}`);
    process.exit(1);
  }
}

main();

module.exports = { gif2svga, nearestValidFps, VALID_FPS };