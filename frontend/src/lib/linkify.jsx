const URL_RE = /(https?:\/\/[^\s<>"'`\]},]+)/g
const MD_LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g

function isSafeHttpUrl(href) {
  const url = (href || '').trim()
  return /^https?:\/\//i.test(url) ? url : ''
}

function linkAnchor(href, label, key, className) {
  const safeHref = isSafeHttpUrl(href)
  if (!safeHref) {
    return label
  }
  return (
    <a
      key={key}
      href={safeHref}
      target="_blank"
      rel="noopener noreferrer"
      className={
        className ||
        'text-accent underline underline-offset-2 hover:opacity-70 break-all transition-opacity duration-150'
      }
    >
      {label}
    </a>
  )
}

function linkifyUrls(text, keyPrefix, className) {
  const parts = text.split(URL_RE)
  return parts.map((part, i) =>
    i % 2 === 1 ? linkAnchor(part, part, `${keyPrefix}-u${i}`, className) : part
  )
}

export function linkify(text, className) {
  if (!text || typeof text !== 'string') return text
  const nodes = []
  let lastIndex = 0
  let match
  const re = new RegExp(MD_LINK_RE.source, 'g')
  let idx = 0
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(...linkifyUrls(text.slice(lastIndex, match.index), `t${idx}`, className))
    }
    nodes.push(linkAnchor(match[2], match[1], `m${idx}`, className))
    lastIndex = match.index + match[0].length
    idx += 1
  }
  if (lastIndex < text.length) {
    nodes.push(...linkifyUrls(text.slice(lastIndex), `t${idx}`, className))
  }
  return nodes
}
