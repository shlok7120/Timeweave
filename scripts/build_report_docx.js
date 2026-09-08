/*
 * Convert docs/REPORT.md into a formatted Word document.
 *
 *   node scripts/build_report_docx.js
 *
 * The markdown is generated from the benchmark CSV by scripts/make_report.py,
 * so the chain data -> markdown -> .docx never needs a human to retype a number.
 */

const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType,
  LevelFormat, ImageRun, PageBreak,
} = require('docx');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'docs', 'REPORT.md');
const OUT = path.join(ROOT, 'docs', 'TimeWeave_Project_Report.docx');

const CONTENT_W = 9026;          // A4 minus 1" margins, in DXA
const MAX_IMG_PX = 600;
const ACCENT = '1F3864';
const ACCENT2 = '2E5C8A';
const GREY = '444444';

// ---------- helpers -------------------------------------------------------
function pngSize(file) {
  const b = fs.readFileSync(file);
  if (b.length > 24 && b.toString('ascii', 1, 4) === 'PNG') {
    return { w: b.readUInt32BE(16), h: b.readUInt32BE(20) };
  }
  return { w: MAX_IMG_PX, h: Math.round(MAX_IMG_PX * 0.55) };
}

function inline(text, base = {}) {
  const runs = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) runs.push(new TextRun({ ...base, text: text.slice(last, m.index) }));
    const tok = m[0];
    if (tok.startsWith('**')) runs.push(new TextRun({ ...base, text: tok.slice(2, -2), bold: true }));
    else if (tok.startsWith('`')) runs.push(new TextRun({
      ...base, text: tok.slice(1, -1), font: 'Consolas', size: 19, color: 'A31515' }));
    else runs.push(new TextRun({ ...base, text: tok.slice(1, -1), italics: true }));
    last = m.index + tok.length;
  }
  if (last < text.length) runs.push(new TextRun({ ...base, text: text.slice(last) }));
  if (!runs.length) runs.push(new TextRun({ ...base, text: '' }));
  return runs;
}

function codeLine(text) {
  return new Paragraph({
    children: [new TextRun({ text: text || ' ', font: 'Consolas', size: 16, color: '1A1A1A' })],
    spacing: { after: 0, line: 240 },
    shading: { type: ShadingType.CLEAR, fill: 'F4F5F7' },
    indent: { left: 180, right: 180 },
  });
}

function cell(text, isHeader, width) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: { type: ShadingType.CLEAR, fill: isHeader ? ACCENT : 'FFFFFF' },
    margins: { top: 70, bottom: 70, left: 110, right: 110 },
    children: [new Paragraph({
      children: inline(text, isHeader ? { bold: true, color: 'FFFFFF', size: 17 } : { size: 17 }),
      spacing: { after: 0, line: 230 },
    })],
  });
}

function buildTable(rows) {
  const cols = Math.max(...rows.map(r => r.length));
  const w = Math.floor(CONTENT_W / cols);
  const widths = Array(cols).fill(w);
  widths[cols - 1] = CONTENT_W - w * (cols - 1);
  const b = { style: BorderStyle.SINGLE, size: 4, color: 'C6CBD4' };
  return new Table({
    columnWidths: widths,
    width: { size: CONTENT_W, type: WidthType.DXA },
    borders: { top: b, bottom: b, left: b, right: b, insideHorizontal: b, insideVertical: b },
    rows: rows.map((cells, i) => new TableRow({
      tableHeader: i === 0,
      children: Array.from({ length: cols }, (_, c) =>
        cell(cells[c] !== undefined ? cells[c] : '', i === 0, widths[c])),
    })),
  });
}

// ---------- parse ---------------------------------------------------------
const lines = fs.readFileSync(SRC, 'utf8').split('\n');
const children = [];
let i = 0, ordered = 0, seenTitle = false;

const splitRow = (l) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(s => s.trim());

while (i < lines.length) {
  const t = lines[i].trim();

  if (t === '') { i++; continue; }

  if (/^---+$/.test(t)) {
    children.push(new Paragraph({
      text: '', border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: 'C6CBD4' } },
      spacing: { before: 120, after: 240 } }));
    i++; continue;
  }

  // image
  const img = t.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
  if (img) {
    const file = path.resolve(path.dirname(SRC), img[2]);
    if (fs.existsSync(file)) {
      const { w, h } = pngSize(file);
      const width = Math.min(MAX_IMG_PX, w);
      const height = Math.round(h * (width / w));
      children.push(new Paragraph({
        children: [new ImageRun({ type: 'png', data: fs.readFileSync(file),
                                  transformation: { width, height } })],
        alignment: AlignmentType.CENTER, spacing: { before: 160, after: 60 },
      }));
      if (img[1]) children.push(new Paragraph({
        children: [new TextRun({ text: img[1], italics: true, size: 17, color: GREY })],
        alignment: AlignmentType.CENTER, spacing: { after: 220 },
      }));
    }
    i++; continue;
  }

  // code fence
  if (t.startsWith('```')) {
    i++;
    const buf = [];
    while (i < lines.length && !lines[i].trim().startsWith('```')) { buf.push(lines[i]); i++; }
    i++;
    children.push(new Paragraph({ text: '', spacing: { after: 60 } }));
    buf.forEach(l => children.push(codeLine(l)));
    children.push(new Paragraph({ text: '', spacing: { after: 170 } }));
    continue;
  }

  // table
  if (t.startsWith('|')) {
    const rows = [];
    while (i < lines.length && lines[i].trim().startsWith('|')) {
      const cs = splitRow(lines[i]);
      if (!cs.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cs);
      i++;
    }
    children.push(buildTable(rows));
    children.push(new Paragraph({ text: '', spacing: { after: 220 } }));
    continue;
  }

  // headings
  const hm = t.match(/^(#{1,6})\s+(.*)$/);
  if (hm) {
    const level = hm[1].length, text = hm[2];
    if (level === 1) {
      children.push(new Paragraph({
        children: [new TextRun({ text, bold: true, size: 46, color: ACCENT })],
        alignment: AlignmentType.CENTER, spacing: { before: 1400, after: 120 },
        heading: HeadingLevel.TITLE }));
      seenTitle = true;
    } else if (level === 3 && !children.some(c => c.__afterTitle)) {
      const p = new Paragraph({
        children: [new TextRun({ text, size: 24, color: ACCENT2, italics: true })],
        alignment: AlignmentType.CENTER, spacing: { after: 300 } });
      p.__afterTitle = true;
      children.push(p);
    } else {
      const isSection = level === 2;
      children.push(new Paragraph({
        children: [new TextRun({ text, bold: true, size: isSection ? 28 : 23, color: ACCENT })],
        heading: isSection ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2,
        spacing: { before: isSection ? 380 : 280, after: 150 },
        pageBreakBefore: isSection && /^\d+\. /.test(text) && text.startsWith('1. ') === false
                         ? false : false,
      }));
    }
    i++; continue;
  }

  // lists
  if (/^[-*]\s+/.test(t)) {
    while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
      children.push(new Paragraph({
        children: inline(lines[i].trim().replace(/^[-*]\s+/, '')),
        numbering: { reference: 'bullets', level: 0 },
        spacing: { after: 90, line: 280 } }));
      i++;
    }
    children.push(new Paragraph({ text: '', spacing: { after: 60 } }));
    continue;
  }
  if (/^\d+\.\s+/.test(t)) {
    ordered++;
    while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
      children.push(new Paragraph({
        children: inline(lines[i].trim().replace(/^\d+\.\s+/, '')),
        numbering: { reference: 'numbers', level: 0, instance: ordered },
        spacing: { after: 90, line: 280 } }));
      i++;
    }
    children.push(new Paragraph({ text: '', spacing: { after: 60 } }));
    continue;
  }

  children.push(new Paragraph({
    children: inline(t), spacing: { after: 160, line: 290 },
    alignment: AlignmentType.JUSTIFIED }));
  i++;
}

// ---------- document -----------------------------------------------------
const doc = new Document({
  creator: 'Shlok Patel and Khush Patel',
  title: 'TimeWeave — Project Report',
  description: 'Automated timetable generation using constraint satisfaction and local search',
  styles: { default: { document: { run: { font: 'Calibri', size: 21, color: '1A1A1A' } } } },
  numbering: {
    config: [
      { reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•',
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 460, hanging: 260 } } } }] },
      { reference: 'numbers', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.',
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 460, hanging: 260 } } } }] },
    ],
  },
  sections: [{
    properties: { page: { margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(OUT, buf);
  console.log('written', OUT, (buf.length / 1024).toFixed(0) + ' KB');
});
