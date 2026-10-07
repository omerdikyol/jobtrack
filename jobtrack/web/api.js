export async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg).join("; ")
          : response.statusText;
    throw new Error(message || "The server could not complete this request.");
  }
  return body;
}
export const write = (path, body, method = "POST") =>
  api(path, { method, body: JSON.stringify(body) });
