// A small, safe Markdown renderer (no HTML injection): fences, headings, tables, lists, paragraphs,
// inline code and bold.
function inline(text, key) {
  const parts = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    parts.push(tok.startsWith("`") ? <code key={`${key}-${m.index}`}>{tok.slice(1, -1)}</code>
      : <strong key={`${key}-${m.index}`}>{tok.slice(2, -2)}</strong>);
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
const isList = (l) => /^\s*([-*]|\d+\.)\s/.test(l);

export default function Markdown({ text }) {
  const lines = (text || "").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const body = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      i++;
      out.push(<pre key={out.length} className="code">{body.join("\n")}</pre>);
    } else if (/^#{1,6}\s/.test(line)) {
      const level = Math.min(line.match(/^#+/)[0].length + 1, 6);
      const H = `h${level}`;
      out.push(<H key={out.length} className="md-h">{inline(line.replace(/^#+\s/, ""), i)}</H>);
      i++;
    } else if (/^\s*\|/.test(line)) {
      const rows = [];
      for (; i < lines.length && /^\s*\|/.test(lines[i]); i++) if (!/^\s*\|?\s*:?-{2,}/.test(lines[i])) rows.push(cells(lines[i]));
      const [head, ...body] = rows;
      out.push(
        <div key={out.length} className="table-wrap">
          <table className="table table-compact md-table">
            <thead><tr>{head.map((c, k) => <th key={k}>{inline(c, k)}</th>)}</tr></thead>
            <tbody>{body.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c, j)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else if (isList(line)) {
      const items = [];
      for (; i < lines.length && (isList(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length)); i++) {
        if (isList(lines[i])) items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s/, ""));
        else items[items.length - 1] += " " + lines[i].trim();
      }
      out.push(<ul key={out.length}>{items.map((it, k) => <li key={k}>{inline(it, k)}</li>)}</ul>);
    } else if (!line.trim()) {
      i++;
    } else {
      const para = [];
      for (; i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|\s*\|)/.test(lines[i]) && !isList(lines[i]); i++) para.push(lines[i]);
      out.push(<p key={out.length}>{para.map((l, k) => <span key={k}>{k > 0 && <br />}{inline(l, k)}</span>)}</p>);
    }
  }
  return <div className="md">{out}</div>;
}
