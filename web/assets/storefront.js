import {api, currency, element} from "./shared.js";

const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const messages = document.querySelector("#messages");
const catalog = document.querySelector("#catalog");
const catalogStatus = document.querySelector("#catalog-status");
const categoryFilters = document.querySelector("#category-filters");
const newChat = document.querySelector("#new-chat");
const sessionKey = "grove-shopper-session";
let threadId = crypto.randomUUID();
let busy = false;
let products = [];
let selectedCategory = "";

function bubble(role, text) {
  const entry = element("div", undefined, `bubble ${role}`);
  entry.append(element("strong", role === "shop" ? "Grove Supply" : "You"), element("p", text));
  messages.append(entry);
  messages.scrollTop = messages.scrollHeight;
  return entry;
}

function resetChat() {
  threadId = crypto.randomUUID();
  messages.replaceChildren();
  bubble("shop", "Hi! Ask me to compare products, check stock, or explain our delivery and returns.");
}

async function shopperChat(prompt) {
  let token = localStorage.getItem(sessionKey);
  if (!token) {
    const session = await api("/shopper/session", {method: "POST"});
    token = localStorage.getItem(sessionKey) || session.token;
    localStorage.setItem(sessionKey, token);
  }
  try {
    return await api("/shopper/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json", Authorization: `Bearer ${token}`},
      body: JSON.stringify({thread_id: threadId, message: prompt})
    });
  } catch (error) {
    if (error.status === 401 && localStorage.getItem(sessionKey) === token) localStorage.removeItem(sessionKey);
    throw error;
  }
}

async function send(prompt, label = prompt) {
  if (busy || !prompt.trim()) return;
  busy = true;
  newChat.disabled = true;
  form.querySelector("button").disabled = true;
  bubble("user", label);
  const pending = bubble("shop", "Checking…");
  try {
    const result = await shopperChat(prompt);
    pending.querySelector("p").textContent = result.reply;
    const confirmation = result.tool_calls.some(call => call.name === "lookup_catalog") &&
      prompt.startsWith("/buy ") && result.reply.match(/\bCONFIRM [A-F0-9]{8}\b/);
    if (confirmation) {
      const confirm = element("button", "Confirm demo order");
      const cancel = element("button", "Cancel", "secondary");
      const controls = element("div", undefined, "suggestions");
      controls.append(confirm, cancel);
      pending.append(controls);
      confirm.addEventListener("click", () => {
        if (busy) return;
        controls.remove();
        void send(confirmation[0], "Confirm demo order");
      });
      cancel.addEventListener("click", () => {
        if (busy) return;
        controls.remove();
        void send("CANCEL", "Cancel order");
      });
    }
    if (result.tool_calls.some(call => call.name === "place_demo_order")) void loadCatalog();
  } catch (error) {
    if (error.status === 401) {
      resetChat();
      bubble("shop", "Your shopper session ended. Send your message again to start a new session.");
    } else {
      pending.querySelector("p").textContent = error.message;
    }
  } finally {
    busy = false;
    newChat.disabled = false;
    form.querySelector("button").disabled = false;
    messages.scrollTop = messages.scrollHeight;
  }
}

function renderCatalog() {
  const visible = selectedCategory ? products.filter(product => product.category === selectedCategory) : products;
  document.querySelector("#product-count").textContent = `${visible.length} ${visible.length === 1 ? "product" : "products"}`;
  for (const button of categoryFilters.children) {
    button.setAttribute("aria-pressed", String(button.dataset.category === selectedCategory));
  }
  catalog.replaceChildren(...visible.map(product => {
    const card = element("article", undefined, "product");
    const art = element("div", undefined, `product-art ${product.art}-backdrop`);
    art.setAttribute("aria-hidden", "true");
    art.append(element("div", undefined, `shape ${product.art}`));
    const details = element("div", undefined, "product-details");
    const buy = element("button", "Order", "secondary");
    buy.disabled = product.stock === 0;
    buy.setAttribute("aria-label", `Order ${product.name}`);
    buy.addEventListener("click", () => {
      document.querySelector("#assistant").scrollIntoView({block: "start"});
      void send(`/buy ${product.sku} 1`, `Order one ${product.name}`);
    });
    const pricing = element("div", undefined, "product-footer");
    pricing.append(element("strong", currency(product.cents)), buy);
    details.append(element("p", `${product.category} · ${product.stock ? `${product.stock} in stock` : "Sold out"}`, "small"),
      element("h3", product.name), element("p", product.description), pricing);
    card.append(art, details);
    return card;
  }));
}

async function loadCatalog() {
  try {
    products = await api("/catalog");
    const categories = [...new Set(products.map(product => product.category))].sort();
    if (!categories.includes(selectedCategory)) selectedCategory = "";
    categoryFilters.replaceChildren(...["", ...categories].map(category => {
      const button = element("button", category || "All products", "secondary");
      button.type = "button";
      button.dataset.category = category;
      button.addEventListener("click", () => {
        selectedCategory = category;
        renderCatalog();
      });
      return button;
    }));
    catalogStatus.textContent = products.length ? "" : "No products are available right now.";
    renderCatalog();
  } catch (error) {
    catalogStatus.textContent = error.message;
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  if (busy) return;
  const prompt = input.value.trim();
  if (!prompt) return;
  input.value = "";
  void send(prompt);
});
newChat.addEventListener("click", resetChat);
document.querySelectorAll("[data-prompt]").forEach(button => {
  button.addEventListener("click", () => void send(button.dataset.prompt, button.textContent));
});
resetChat();
void loadCatalog();
