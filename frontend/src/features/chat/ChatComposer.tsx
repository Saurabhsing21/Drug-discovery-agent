"use client";

import { useState } from "react";

export function ChatComposer({
  disabled,
  placeholder = "Ask a follow-up about specific mutation types…",
  onSend,
}: {
  disabled?: boolean;
  placeholder?: string;
  onSend?: (message: string, opts: { useWebSearch: boolean }) => void;
}) {
  const [message, setMessage] = useState("");
  const [useWebSearch] = useState(true);

  const send = () => {
    if (disabled) return;
    if (!message.trim()) return;
    onSend?.(message.trim(), { useWebSearch });
    setMessage("");
  };

  return (
    <div className="flex items-center gap-3 rounded-full border border-white/10 bg-neutral-900/60 px-3 py-2 shadow-xl shadow-black/30 backdrop-blur">
      <button
        type="button"
        className="h-10 w-10 shrink-0 rounded-full bg-white/5 text-sm text-neutral-200 hover:bg-white/10 disabled:opacity-50"
        disabled={disabled}
        title="Attach data (not wired)"
      >
        +
      </button>

      <input
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent px-1 text-sm text-neutral-100 outline-none placeholder:text-neutral-500 disabled:opacity-50"
        disabled={disabled}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
      />

      <button
        type="button"
        onClick={send}
        disabled={disabled || message.trim().length === 0}
        className="h-10 shrink-0 rounded-full bg-white px-4 text-sm font-medium text-neutral-900 disabled:opacity-50"
      >
        Send
      </button>
    </div>
  );
}
