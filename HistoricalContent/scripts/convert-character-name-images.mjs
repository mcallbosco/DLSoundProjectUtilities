#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import sharp from 'sharp';

function parseArgs(argv) {
  const options = { source: '', output: '', maxHeight: 512 };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--source') options.source = path.resolve(argv[++index]);
    else if (argument === '--output') options.output = path.resolve(argv[++index]);
    else if (argument === '--max-height') options.maxHeight = Number(argv[++index]);
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!options.source || !options.output) {
    throw new Error('--source and --output are required.');
  }
  if (!Number.isInteger(options.maxHeight) || options.maxHeight < 64 || options.maxHeight > 4096) {
    throw new Error('--max-height must be an integer between 64 and 4096.');
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

function imageKey(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  const stem = path.basename(filePath, extension).toLowerCase();
  if (extension === '.svg' && stem.endsWith('_localized')) {
    return stem.slice(0, -'_localized'.length);
  }
  if (stem.includes('team1_patron_logo')) return 'patron_male';
  if (stem.includes('team2_patron_logo')) return 'patron_female';
  return null;
}

function outputStem(key) {
  if (key === 'patron_male') return 'team1';
  if (key === 'patron_female') return 'team2';
  return `${key}_localized`;
}

function sanitizeSvg(svg) {
  return svg.replace(/<svg\b[^>]*>/is, (openingTag) => {
    let foundDefaultNamespace = false;
    return openingTag.replace(/\sxmlns\s*=\s*(["'])[^"']*\1/gi, (attribute) => {
      if (!foundDefaultNamespace) {
        foundDefaultNamespace = true;
        return attribute;
      }
      return '';
    });
  });
}

async function imageInput(filePath) {
  if (path.extname(filePath).toLowerCase() !== '.svg') return filePath;
  const source = await fs.readFile(filePath, 'utf8');
  return Buffer.from(sanitizeSvg(source), 'utf8');
}

async function encode(filePath, maxHeight) {
  const input = await imageInput(filePath);
  const pipeline = () => sharp(input, { density: 96 })
    .resize({ height: maxHeight, withoutEnlargement: true })
    .ensureAlpha();

  // These assets are monochrome alpha masks. Preserve the anti-aliased alpha
  // edge and let libwebp choose the smaller of exact and near-lossless output.
  const [lossless, nearLossless] = await Promise.all([
    pipeline().webp({ lossless: true, effort: 6 }).toBuffer({ resolveWithObject: true }),
    pipeline().webp({ nearLossless: true, quality: 95, alphaQuality: 100, effort: 6 })
      .toBuffer({ resolveWithObject: true }),
  ]);
  return nearLossless.data.length < lossless.data.length ? nearLossless : lossless;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const sourceStat = await fs.stat(options.source).catch(() => null);
  if (!sourceStat?.isDirectory()) throw new Error(`Source directory does not exist: ${options.source}`);
  assertInside(path.dirname(options.output), options.output, 'output directory');
  await fs.mkdir(options.output, { recursive: true });

  const candidates = new Map();
  for (const filePath of await walk(options.source)) {
    const key = imageKey(filePath);
    if (key && !candidates.has(key)) candidates.set(key, filePath);
  }

  const images = {};
  const warnings = [];
  for (const [key, filePath] of [...candidates].sort(([left], [right]) => left.localeCompare(right))) {
    try {
      const encoded = await encode(filePath, options.maxHeight);
      const digest = createHash('sha256').update(encoded.data).digest('hex');
      const filename = `${outputStem(key)}.${digest}.webp`;
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
  process.stderr.write(`convert-character-name-images: ${error.message}\n`);
  process.exit(1);
});
