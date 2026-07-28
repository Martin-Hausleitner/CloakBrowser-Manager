export function redactProxyLabel(proxy: string | null | undefined) {
  return proxy?.trim() ? "Configured" : "Direct";
}
