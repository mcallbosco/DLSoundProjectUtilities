#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import sharp from 'sharp';

const BACKGROUND_SUFFIX = '_bg_psd';

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function rgbToHsl(red, green, blue) {
  const r = red / 255;
  const g = green / 255;
  const b = blue / 255;
  const maximum = Math.max(r, g, b);
  const minimum = Math.min(r, g, b);
  const delta = maximum - minimum;
  const lightness = (maximum + minimum) / 2;
  if (delta === 0) return { h: 0, s: 0, l: lightness };

  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  let hue = 0;
  if (maximum === r) hue = ((g - b) / delta) % 6;
  else if (maximum === g) hue = (b - r) / delta + 2;
  else hue = (r - g) / delta + 4;
  return { h: ((hue * 60) + 360) % 360, s: saturation, l: lightness };
}

function hslToHex({ h, s, l }) {
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const segment = h / 60;
  const secondary = chroma * (1 - Math.abs((segment % 2) - 1));
  let r = 0;
  let g = 0;
  let b = 0;
  if (segment < 1) [r, g] = [chroma, secondary];
  else if (segment < 2) [r, g] = [secondary, chroma];
  else if (segment < 3) [g, b] = [chroma, secondary];
  else if (segment < 4) [g, b] = [secondary, chroma];
  else if (segment < 5) [r, b] = [secondary, chroma];
  else [r, b] = [chroma, secondary];
  const match = l - chroma / 2;
  const toHex = (channel) => Math.round((channel + match) * 255)
    .toString(16)
    .padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function deriveMutedAccentColor(pixels) {
  const bins = Array.from({ length: 12 }, () => ({
    weight: 0,
    hueX: 0,
    hueY: 0,
    saturation: 0,
    lightness: 0,
  }));
  let neutralWeight = 0;
  let neutralLightness = 0;
  for (let index = 0; index + 3 < pixels.length; index += 4) {
    const alpha = pixels[index + 3] / 255;
    if (alpha < 0.25) continue;
    const hsl = rgbToHsl(pixels[index], pixels[index + 1], pixels[index + 2]);
    if (hsl.l < 0.05 || hsl.l > 0.92) continue;
    neutralWeight += alpha;
    neutralLightness += hsl.l * alpha;
    if (hsl.s < 0.1) continue;
    const weight = alpha * (0.25 + hsl.s) * (0.65 + Math.min(hsl.l, 0.65));
    const bin = bins[Math.floor(hsl.h / 30) % bins.length];
    const radians = hsl.h * Math.PI / 180;
    bin.weight += weight;
    bin.hueX += Math.cos(radians) * weight;
    bin.hueY += Math.sin(radians) * weight;
    bin.saturation += hsl.s * weight;
    bin.lightness += hsl.l * weight;
  }
  const dominant = bins.reduce((best, candidate) => (
    candidate.weight > best.weight ? candidate : best
  ));
  if (dominant.weight === 0) {
    if (neutralWeight === 0) return '#29433a';
    return hslToHex({
      h: 180,
      s: 0.08,
      l: clamp((neutralLightness / neutralWeight) * 0.58, 0.18, 0.31),
    });
  }
  return hslToHex({
    h: (Math.atan2(dominant.hueY, dominant.hueX) * 180 / Math.PI + 360) % 360,
    s: clamp((dominant.saturation / dominant.weight) * 0.78, 0.28, 0.52),
    l: clamp((dominant.lightness / dominant.weight) * 0.58, 0.18, 0.31),
  });
}

function parseArgs(argv) {
  const options = { source: '', output: '', width: 1024 };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--source') options.source = path.resolve(argv[++index]);
    else if (argument === '--output') options.output = path.resolve(argv[++index]);
    else if (argument === '--width') options.width = Number(argv[++index]);
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!options.source || !options.output) throw new Error('--source and --output are required.');
  if (!Number.isInteger(options.width) || options.width < 64 || options.width > 4096) {
    throw new Error('--width must be an integer between 64 and 4096.');
  }
  return options;
}

function assertInside(parent, target, label) {
  const relative = path.relative(path.resolve(parent), path.resolve(target));
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    throw new Error(`${label} resolves outside ${parent}: ${target}`);
  }
}

async function walk(directory) {
  const files = [];
  const pending = [directory];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of await fs.readdir(current, { withFileTypes: true })) {
      const item = path.join(current, entry.name);
      if (entry.isDirectory()) pending.push(item);
      else if (entry.isFile()) files.push(item);
    }
  }
  return files.sort((left, right) => left.localeCompare(right));
}

function backgroundKey(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  if (!['.png', '.webp', '.jpg', '.jpeg'].includes(extension)) return null;
  const stem = path.basename(filePath, extension).toLowerCase();
  return stem.endsWith(BACKGROUND_SUFFIX)
    ? stem.slice(0, -BACKGROUND_SUFFIX.length)
    : null;
}

async function encode(filePath, width) {
  const metadata = await sharp(filePath).metadata();
  if (!metadata.width || !metadata.height) throw new Error('Image dimensions are unavailable.');
  const left = Math.floor(metadata.width / 2);
  return sharp(filePath)
    .extract({ left, top: 0, width: metadata.width - left, height: metadata.height })
    .resize({ width, withoutEnlargement: true })
    .webp({ quality: 84, effort: 6, smartSubsample: true })
    .toBuffer({ resolveWithObject: true });
}

async function sampleAccentColor(encodedImage) {
  const pixels = await sharp(encodedImage)
    .resize(32, 32, { fit: 'fill' })
    .ensureAlpha()
    .raw()
    .toBuffer();
  return deriveMutedAccentColor(pixels);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const sourceStat = await fs.stat(options.source).catch(() => null);
  if (!sourceStat?.isDirectory()) throw new Error(`Source directory does not exist: ${options.source}`);
  assertInside(path.dirname(options.output), options.output, 'output directory');
  await fs.mkdir(options.output, { recursive: true });

  const candidates = new Map();
  for (const filePath of await walk(options.source)) {
    const key = backgroundKey(filePath);
    if (key && !candidates.has(key)) candidates.set(key, filePath);
  }

  const images = {};
  const warnings = [];
  for (const [key, filePath] of [...candidates].sort(([left], [right]) => left.localeCompare(right))) {
    try {
      const encoded = await encode(filePath, options.width);
      const digest = createHash('sha256').update(encoded.data).digest('hex');
      const filename = `${key}.${digest}.webp`;
      const destination = path.join(options.output, filename);
      assertInside(options.output, destination, 'encoded image');
      const temporary = `${destination}.tmp`;
      await fs.writeFile(temporary, encoded.data);
      await fs.rename(temporary, destination);
      images[key] = {
        file: filename,
        width: encoded.info.width,
        height: encoded.info.height,
        sha256: digest,
        accentColor: await sampleAccentColor(encoded.data),
      };
    } catch (error) {
      warnings.push({
        file: path.relative(options.source, filePath).split(path.sep).join('/'),
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  process.stdout.write(`${JSON.stringify({ images, warnings })}\n`);
}

main().catch((error) => {
  process.stderr.write(`convert-character-select-backgrounds: ${error.message}\n`);
  process.exit(1);
});
