export const currency = cents => new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD"
}).format(cents / 100);

export function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
}

export async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = new Error(typeof body?.detail === "string" ? body.detail : "The store is temporarily unavailable.");
    error.status = response.status;
    throw error;
  }
  return response.json();
}
