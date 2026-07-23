import { useEffect, useState } from "react";
import { api, type ProfileOpenLinks } from "../lib/api";

interface SessionStreamButtonsProps {
  profileId: string;
  running: boolean;
  className?: string;
}

/** VNC + CDP fullscreen entry points from `/api/profiles/{id}/open-links`. */
export function SessionStreamButtons({
  profileId,
  running,
  className = "",
}: SessionStreamButtonsProps) {
  const [links, setLinks] = useState<ProfileOpenLinks | null>(null);

  useEffect(() => {
    if (!running) {
      setLinks(null);
      return;
    }
    let cancelled = false;
    void api
      .getProfileOpenLinks(profileId, "local", "cdp")
      .then((next) => {
        if (!cancelled) setLinks(next);
      })
      .catch(() => {
        if (!cancelled) setLinks(null);
      });
    return () => {
      cancelled = true;
    };
  }, [profileId, running]);

  if (!running || !links) return null;

  const cdpUrl = links.live_url || links.cdp_fullscreen_url;
  const vncUrl = links.vnc_fullscreen_url;
  if (!cdpUrl && !vncUrl) return null;

  return (
    <div className={`flex items-center gap-1.5 ${className}`}>
      {cdpUrl ? (
        <a
          className="rounded border border-border px-2 py-1 text-[11px] text-emerald-300 hover:bg-surface-2"
          href={cdpUrl}
          target="_blank"
          rel="noreferrer"
          title="CDP fullscreen (open-links live_url)"
        >
          CDP FS
        </a>
      ) : null}
      {vncUrl ? (
        <a
          className="rounded border border-border px-2 py-1 text-[11px] text-sky-300 hover:bg-surface-2"
          href={vncUrl}
          title="VNC fullscreen (open-links vnc_fullscreen_url)"
        >
          VNC FS
        </a>
      ) : null}
    </div>
  );
}
