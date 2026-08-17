import type { FoliateView } from './FoliateEngine';

export function moonAnchorCandidates(value: string): string[] {
  const normalized = value.replace(/\s+/gu, ' ').trim();
  if (!normalized) return [];
  const candidates = [120, 80, 48, 32]
    .filter((length) => normalized.length >= length)
    .map((length) => normalized.slice(0, length).trim());
  if (!candidates.includes(normalized)) candidates.unshift(normalized);
  return [...new Set(candidates)].filter((item) => item.length >= 24);
}

export async function findMoonAnchorCfi(
  view: FoliateView, anchor: string, section?: number | null,
): Promise<string | null> {
  const candidates = moonAnchorCandidates(anchor);
  const hinted = Number.isInteger(section) && Number(section) >= 0 ? Number(section) : null;
  const scan = async (query: string, index?: number): Promise<string | null> => {
    try {
      for await (const raw of view.search({
        query, index, matchCase: false, matchDiacritics: false,
      })) {
        if (raw === 'done') break;
        const item = raw as { cfi?: string; subitems?: Array<{ cfi: string }> };
        const cfi = item.cfi ?? item.subitems?.[0]?.cfi;
        if (cfi) return cfi;
      }
      return null;
    } catch {
      return null;
    } finally {
      view.clearSearch();
    }
  };
  if (hinted !== null) {
    for (const query of candidates) {
      const cfi = await scan(query, hinted);
      if (cfi) return cfi;
    }
  }
  for (const query of candidates.slice(-2)) {
    const cfi = await scan(query);
    if (cfi) return cfi;
  }
  return null;
}
