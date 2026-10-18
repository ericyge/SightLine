// Remembers the backend address between app reloads (a small text file in the app's document folder).
// Never throws: if the file cannot be read or written, the app just asks for the address again.
import { File, Paths } from 'expo-file-system';

const file = () => new File(Paths.document, 'sightline-server.txt');

/** The last address that connected, or '' if none was saved. */
export function loadServer(): string {
  try {
    const f = file();
    return f.exists ? f.textSync().trim() : '';
  } catch {
    return '';
  }
}

/** Save an address that just connected successfully. */
export function saveServer(url: string): void {
  try {
    const f = file();
    if (!f.exists) f.create();
    f.write(url.trim());
  } catch {
    // not fatal: the address just will not be remembered
  }
}
