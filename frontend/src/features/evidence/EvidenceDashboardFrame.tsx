"use client";

import { useEffect, useRef } from "react";

export function EvidenceDashboardFrame({ src, className }: { src: string; className?: string }) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (!event?.data || event.data.type !== "evidence-dashboard-height") return;
      const height = Number(event.data.height);
      if (!iframeRef.current || !Number.isFinite(height)) return;
      iframeRef.current.style.height = `${Math.max(height, 480)}px`;
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  return (
    <iframe
      ref={iframeRef}
      title="Evidence contribution dashboard"
      src={src}
      className={className ?? "w-full rounded-xl bg-transparent"}
    />
  );
}
