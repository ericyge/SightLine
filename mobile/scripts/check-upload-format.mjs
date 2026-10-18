// Run with: npm run check:upload
// Proves, without a phone, that our file parts are accepted by Expo SDK 57's own multipart encoder
// (node_modules/expo/src/winter/fetch/convertFormData.ts), and that the old React Native
// {uri, name, type} part is rejected with the error seen on the phone.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeFilePart } from '../src/filePart.ts';

const require = createRequire(import.meta.url);
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const babel = require(join(root, 'node_modules/@babel/core'));

// Transpile Expo's TypeScript encoder (and its one helper) to CommonJS in a temp folder.
const out = join(tmpdir(), 'sightline-convertformdata');
const compile = (rel, target) => {
  const file = join(root, 'node_modules/expo/src', rel);
  const { code } = babel.transformSync(readFileSync(file, 'utf8'), {
    filename: file,
    babelrc: false,
    configFile: false,
    presets: [[join(root, 'node_modules/@babel/preset-typescript'), { onlyRemoveTypeImports: true }]],
    plugins: [join(root, 'node_modules/@babel/plugin-transform-modules-commonjs')],
  });
  mkdirSync(dirname(join(out, target)), { recursive: true });
  writeFileSync(join(out, target), code);
};
compile('utils/blobUtils.ts', 'utils/blobUtils.js');
compile('winter/fetch/convertFormData.ts', 'winter/fetch/convertFormData.js');
const { convertFormDataAsync } = require(join(out, 'winter/fetch/convertFormData.js'));

/** A FormData stand-in shaped like React Native's after Expo's patch: entries() yields [name, value]. */
const formWith = (...parts) => ({ entries: () => parts[Symbol.iterator]() });
const decode = (u8) => new TextDecoder().decode(u8);

// 1. The old format reproduces the phone's error.
await assert.rejects(
  convertFormDataAsync(formWith(['frame', { uri: 'file:///x/frame.jpg', name: 'frame.jpg', type: 'image/jpeg' }])),
  /Unsupported FormDataPart implementation/,
);
console.log('ok: the old {uri, name, type} part is rejected (this was the error on the phone)');

// 2. Our part is accepted, and arrives with the right filename, type and exact bytes.
const jpeg = Uint8Array.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 0xff, 0xd9]);
const part = makeFilePart({ bytes: async () => jpeg }, 'frame.jpg', 'image/jpeg');
const { body, boundary } = await convertFormDataAsync(
  formWith(['room', 'kitchen'], ['frame', part]), 'testboundary');
assert.equal(boundary, 'testboundary');
const text = decode(body);
assert.match(text, /content-disposition: form-data; name="room"\r\n\r\nkitchen\r\n/);
assert.match(text, /content-disposition: form-data; name="frame"; filename="frame\.jpg"\r\n/);
assert.match(text, /content-type: image\/jpeg\r\n\r\n/);
assert.ok(text.endsWith('--testboundary--\r\n'));
const at = body.findIndex((_, i) => jpeg.every((b, j) => body[i + j] === b));
assert.ok(at > 0, 'the file bytes are in the body');
console.log('ok: our file part is accepted with filename, content-type and the exact bytes');

// 3. Audio (m4a) the same way.
const m4a = Uint8Array.from([0, 0, 0, 0x20, 0x66, 0x74, 0x79, 0x70, 9, 8, 7]);
const audio = makeFilePart({ bytes: async () => m4a }, 'recording.m4a', 'audio/mp4');
const sent = decode((await convertFormDataAsync(formWith(['audio', audio]), 'b')).body);
assert.match(sent, /name="audio"; filename="recording\.m4a"\r\ncontent-type: audio\/mp4\r\n/);
console.log('ok: audio part is accepted too');
