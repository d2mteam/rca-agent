// A small, safe renderer for the agent's text: code fences, inline code, bold, bullet lists.
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

export default function Markdown({ text }) {
  const blocks = (text || "").split(/```[a-z]*\n?/);
  return (
    <div className="md">
      {blocks.map((block, i) =>
        i % 2 === 1 ? (
          <pre key={i} className="code">{block.replace(/\n$/, "")}</pre>
        ) : (
          block.split(/\n{2,}/).filter((p) => p.trim()).map((para, j) => {
            const lines = para.split("\n");
            if (lines.every((l) => /^\s*([-*]|\d+\.)\s/.test(l))) {
              return <ul key={`${i}-${j}`}>{lines.map((l, k) => <li key={k}>{inline(l.replace(/^\s*([-*]|\d+\.)\s/, ""), k)}</li>)}</ul>;
            }
            return <p key={`${i}-${j}`}>{lines.map((l, k) => <span key={k}>{k > 0 && <br />}{inline(l, k)}</span>)}</p>;
          })
        ),
      )}
    </div>
  );
}
