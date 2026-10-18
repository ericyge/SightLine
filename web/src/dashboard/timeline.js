/** Stable sighting IDs are updates, not additional timeline events. */
export function mergeSightings(previous, incoming, limit = 30) {
  const rows = new Map(previous.map((row) => [row.id, row]));
  for (const row of incoming) {
    const old = rows.get(row.id);
    if (!old || Date.parse(row.ts) >= Date.parse(old.ts)) rows.set(row.id, { ...row, key: row.id });
  }
  return [...rows.values()].sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts) || b.id - a.id).slice(0, limit);
}
