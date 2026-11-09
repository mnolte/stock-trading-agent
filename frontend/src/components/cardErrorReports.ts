/** Once-per-page-load dedupe for card error reports
 * (design/DESIGN.md → Failed to draw: logged once per card and page
 * load). The set lives for the JS session — a real reload starts it
 * empty again. */
const reported = new Set<string>();

/** FNV-1a over the error message — the "message fingerprint" of the
 * dedupe key. */
export function cardErrorFingerprint(message: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < message.length; i++) {
    h ^= message.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16);
}

/** The dedupe key: card + page + message fingerprint. */
export function cardErrorKey(
  pathname: string,
  card: string,
  message: string,
): string {
  return `${pathname}|${card}|${cardErrorFingerprint(message)}`;
}

export function cardErrorReported(key: string): boolean {
  return reported.has(key);
}

export function markCardErrorReported(key: string): void {
  reported.add(key);
}

/** Test hook: clears the once-per-page-load report set. */
export function resetCardErrorReports(): void {
  reported.clear();
}
