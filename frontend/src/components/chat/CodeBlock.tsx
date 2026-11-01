import { useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

export default function CodeBlock({ children }: { children?: React.ReactNode }) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    const text = ref.current?.textContent ?? "";
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="group/code relative">
      <pre ref={ref}>{children}</pre>
      <button
        type="button"
        onClick={copy}
        className="absolute right-2 top-2 rounded-md border border-dark-600 bg-dark-800/90 p-1.5 text-gray-400 opacity-0 transition-opacity hover:text-gray-100 focus-visible:opacity-100 group-hover/code:opacity-100"
        title="Copy code"
        aria-label="Copy code"
      >
        {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
      </button>
    </div>
  );
}
