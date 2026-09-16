import "./search-highlight.css";

/** React escapes every fragment; neither FTS markers nor titles are HTML. */
export function SearchHighlight({ text, query = "" }: { text: string; query?: string }) {
  const tokens = [...new Set(query.match(/[\p{L}\p{N}_]+/gu) || [])]
    .sort((a, b) => b.length - a.length);
  // FTS snippets already identify the exact match. Literal query tokens are
  // only needed for titles or the storage fallback without FTS markers.
  const marked = text.includes(">>>") && text.includes("<<<");
  if (!marked && !tokens.length) return <>{text}</>;
  const regex = marked ? />>>(.*?)<<</gs : new RegExp(`(${tokens.join("|")})`, "giu");
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(regex)) {
    parts.push(text.slice(last, match.index));
    parts.push(<mark key={match.index} className="korra-search-match">{match[1]}</mark>);
    last = match.index! + match[0].length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
}
