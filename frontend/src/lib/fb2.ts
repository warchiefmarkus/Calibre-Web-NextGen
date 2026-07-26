export type Fb2Block =
  | { kind: 'heading'; text: string; level: number }
  | { kind: 'paragraph' | 'subtitle' | 'quote'; text: string }
  | { kind: 'image'; src: string; alt: string }
  | { kind: 'break' };

export interface Fb2Document {
  title: string;
  authors: string[];
  blocks: Fb2Block[];
}

function normalizeXmlEncoding(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/_/g, '-');
  const aliases: Record<string, string> = {
    'utf8': 'utf-8',
    'cp1251': 'windows-1251',
    'win-1251': 'windows-1251',
    'windows1251': 'windows-1251',
    'cp1252': 'windows-1252',
    'windows1252': 'windows-1252',
    'utf16': 'utf-16le',
    'utf-16': 'utf-16le',
  };
  return aliases[normalized] ?? normalized;
}

function xmlEncoding(bytes: Uint8Array): string {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return 'utf-8';
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return 'utf-16le';
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return 'utf-16be';
  // XML may be UTF-16 without a BOM; the opening '<' still exposes byte order.
  if (bytes[0] === 0x3c && bytes[1] === 0x00) return 'utf-16le';
  if (bytes[0] === 0x00 && bytes[1] === 0x3c) return 'utf-16be';
  const probe = Array.from(bytes.subarray(0, 512), (byte) => String.fromCharCode(byte)).join('');
  const declared = probe.match(/<\?xml[^>]*encoding\s*=\s*["']\s*([^"']+)/i)?.[1];
  return normalizeXmlEncoding(declared ?? 'utf-8');
}

export function decodeFb2(data: ArrayBuffer): string {
  const bytes = new Uint8Array(data);
  const encoding = xmlEncoding(bytes);
  try {
    return new TextDecoder(encoding, { fatal: true }).decode(bytes);
  } catch {
    // XML defaults to UTF-8. Some legacy FB2 files omit their real CP1251
    // declaration, so use it only after strict UTF-8 decoding has failed.
    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      return new TextDecoder('windows-1251').decode(bytes);
    }
  }
}

function normalizedText(node: Element | null): string {
  return (node?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function children(node: Element): Element[] {
  return Array.from(node.childNodes).filter((child): child is Element => child.nodeType === 1);
}

function firstDescendant(root: Document | Element, name: string): Element | null {
  return Array.from(root.getElementsByTagName('*')).find((node) => node.localName === name) ?? null;
}

function imageHref(node: Element): string {
  return (
    node.getAttributeNS('http://www.w3.org/1999/xlink', 'href')
    || node.getAttribute('l:href')
    || node.getAttribute('href')
    || ''
  ).replace(/^#/, '');
}

export function parseFb2(xml: string): Fb2Document {
  const document = new DOMParser().parseFromString(xml, 'application/xml');
  if (document.getElementsByTagName('parsererror').length) throw new Error('Invalid FB2 document');

  const binaries = new Map<string, string>();
  for (const node of Array.from(document.getElementsByTagName('*'))) {
    if (node.localName !== 'binary') continue;
    const id = node.getAttribute('id') ?? '';
    const mime = node.getAttribute('content-type') ?? 'application/octet-stream';
    const data = (node.textContent ?? '').replace(/\s+/g, '');
    if (id && data) binaries.set(id, `data:${mime};base64,${data}`);
  }

  const titleInfo = Array.from(document.getElementsByTagName('*'))
    .find((node) => node.localName === 'title-info') ?? document.documentElement;
  const title = normalizedText(firstDescendant(titleInfo, 'book-title')) || 'FB2';
  const authors = children(titleInfo)
    .filter((node) => node.localName === 'author')
    .map((node) => ['first-name', 'middle-name', 'last-name']
      .map((name) => normalizedText(children(node).find((part) => part.localName === name) ?? null))
      .filter(Boolean).join(' '))
    .filter(Boolean);

  const blocks: Fb2Block[] = [];
  const walk = (node: Element, sectionLevel = 1, quote = false): void => {
    const name = node.localName;
    if (name === 'title') {
      const text = normalizedText(node);
      if (text) blocks.push({ kind: 'heading', text, level: Math.min(6, sectionLevel + 1) });
      return;
    }
    if (name === 'p') {
      const text = normalizedText(node);
      if (text) blocks.push({ kind: quote ? 'quote' : 'paragraph', text });
      return;
    }
    if (name === 'subtitle') {
      const text = normalizedText(node);
      if (text) blocks.push({ kind: 'subtitle', text });
      return;
    }
    if (name === 'empty-line') {
      blocks.push({ kind: 'break' });
      return;
    }
    if (name === 'image') {
      const key = imageHref(node);
      const src = binaries.get(key);
      if (src) blocks.push({ kind: 'image', src, alt: node.getAttribute('alt') ?? '' });
      return;
    }
    const nextLevel = name === 'section' ? sectionLevel + 1 : sectionLevel;
    const nextQuote = quote || name === 'cite' || name === 'epigraph';
    for (const child of children(node)) walk(child, nextLevel, nextQuote);
  };

  const bodies = Array.from(document.getElementsByTagName('*')).filter((node) => node.localName === 'body');
  for (const body of bodies) walk(body, 0, false);
  return { title, authors, blocks };
}
