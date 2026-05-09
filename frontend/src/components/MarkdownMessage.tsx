import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

const components: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-bold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => <h1 className="font-semibold text-base mt-2 mb-1">{children}</h1>,
  h2: ({ children }) => <h2 className="font-semibold text-sm mt-2 mb-1">{children}</h2>,
  h3: ({ children }) => <h3 className="font-semibold text-sm mt-2 mb-1">{children}</h3>,
  ul: ({ children }) => <ul className="list-disc list-inside mb-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside mb-2">{children}</ol>,
  li: ({ children }) => <li className="mb-0.5">{children}</li>,
  code: ({ children, className }) => {
    const isBlock = !!className
    return isBlock
      ? <code className="block bg-black/40 rounded p-2 overflow-x-auto font-mono text-xs">{children}</code>
      : <code className="bg-black/30 rounded px-1 font-mono text-xs">{children}</code>
  },
  pre: ({ children }) => <pre className="mb-2">{children}</pre>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-current pl-3 opacity-75 mb-2">{children}</blockquote>
  ),
  a: ({ href, children }) => (
    <a href={href} className="underline opacity-80" target="_blank" rel="noreferrer">{children}</a>
  ),
  hr: () => <hr className="border-current opacity-20 my-2" />,
  table: ({ children }) => (
    <table className="text-xs border-collapse mb-2 w-full">{children}</table>
  ),
  th: ({ children }) => (
    <th className="border border-current/30 px-2 py-1 font-semibold text-left">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border border-current/30 px-2 py-1">{children}</td>
  ),
}

interface Props {
  content: string
}

export default function MarkdownMessage({ content }: Props) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {content}
    </ReactMarkdown>
  )
}
