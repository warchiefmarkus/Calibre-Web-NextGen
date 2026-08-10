import type { ReaderSettings } from '../../../lib/queries';

export const FONT_MIN = 75;
export const FONT_MAX = 200;

export const READER_THEME: Record<ReaderSettings['theme'], { background: string; text: string; link: string }> = {
  lightTheme: { background: '#fffdf8', text: '#24211d', link: '#225ea8' },
  sepiaTheme: { background: '#f4ecd8', text: '#433422', link: '#7a4b20' },
  darkTheme: { background: '#202124', text: '#e8eaed', link: '#8ab4f8' },
  blackTheme: { background: '#000000', text: '#eeeeee', link: '#8ab4f8' },
};

const FONT_FAMILY: Record<ReaderSettings['font'], string> = {
  default: 'Georgia, "Times New Roman", serif',
  Yahei: '"Microsoft YaHei", sans-serif',
  SimSun: 'SimSun, serif',
  KaiTi: 'KaiTi, serif',
  Arial: 'Arial, sans-serif',
};

export function readerCss(settings: ReaderSettings, compactViewport = false): string {
  const theme = READER_THEME[settings.theme];
  const compactReflow = compactViewport ? `
    html {
      inline-size: 100% !important;
      min-inline-size: 0 !important;
      max-inline-size: 100% !important;
      overflow-x: hidden !important;
      box-sizing: border-box !important;
    }
    body {
      inline-size: auto !important;
      width: auto !important;
      min-inline-size: 0 !important;
      min-width: 0 !important;
      max-inline-size: 100% !important;
      max-width: 100% !important;
      margin-inline: 0 !important;
      overflow-x: hidden !important;
      box-sizing: border-box !important;
    }
    body * {
      min-inline-size: 0 !important;
      min-width: 0 !important;
      max-inline-size: 100% !important;
      max-width: 100% !important;
      box-sizing: border-box !important;
    }
    h1, h2, h3, h4, h5, h6, p, pre, code { overflow-wrap: anywhere !important; }
    pre, code { white-space: pre-wrap !important; }
    table { inline-size: 100% !important; table-layout: fixed !important; }
  ` : '';
  const textAlignment = settings.justifyText
    ? 'body, p, li, blockquote { text-align: justify !important; text-align-last: auto !important; }'
    : 'body { text-align: left !important; }';
  return `
    :root { color-scheme: ${settings.theme === 'lightTheme' || settings.theme === 'sepiaTheme' ? 'light' : 'dark'}; }
    html, body { background: ${theme.background} !important; color: ${theme.text} !important; }
    body { font-family: ${FONT_FAMILY[settings.font]} !important; font-size: ${settings.fontSize}% !important;
      line-height: ${settings.lineHeight / 100} !important; }
    ${textAlignment}
    a { color: ${theme.link} !important; }
    img, svg, video { max-width: 100% !important; }
    ${compactReflow}
    ::selection { background: rgba(255, 214, 64, .55); }
  `;
}
