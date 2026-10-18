// A file attached to a multipart upload (FormData) in a way Expo SDK 57's fetch accepts.
//
// React Native's old `{ uri, name, type }` part is rejected by SDK 57's fetch with
// "Unsupported FormDataPart implementation": it must be able to read the bytes itself, so a part
// needs a `bytes()` method (a Blob, or an object like this one). `name` and `type` become the
// part's filename and Content-Type. Pure, so scripts/check-upload-format.mjs can test it.

export type ByteSource = { bytes(): Promise<Uint8Array> };

export function makeFilePart(source: ByteSource, name: string, type: string): Blob {
  return { name, type, bytes: () => source.bytes() } as unknown as Blob;
}
