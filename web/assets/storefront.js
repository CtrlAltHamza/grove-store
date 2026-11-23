import {api, currency, element} from "./shared.js";

const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const messages = document.querySelector("#messages");
const catalog = document.querySelector("#catalog");
const catalogStatus = document.querySelector("#catalog-status");
const newChat = document.querySelector("#new-chat");
const userId = localStorage.getItem("grove-shopper") || crypto.randomUUID();
localStorage.setItem("grove-shopper", userId);
let threadId = crypto.randomUUID();
let busy = false;

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

async function send(prompt, label = prompt) {
  if (busy || !prompt.trim()) return;
  busy = true;
  newChat.disabled = true;
  form.querySelector("button").disabled = true;
  bubble("user", label);
  const pending = bubble("shop", "Checking…");
  try {
    const result = await api("/chat", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({user_id: userId, thread_id: threadId, message: prompt})
    });
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
    pending.querySelector("p").textContent = error.message;
  } finally {
    busy = false;
    newChat.disabled = false;
    form.querySelector("button").disabled = false;
    messages.scrollTop = messages.scrollHeight;
  }
}

async function loadCatalog() {
  try {
    const products = await api("/catalog");
    document.querySelector("#product-count").textContent = `${products.length} products`;
    catalogStatus.textContent = "";
    catalog.replaceChildren(...products.map(product => {
      const card = element("article", undefined, "product");
      const art = element("div", undefined, `product-art ${product.art}-backdrop`);
      art.setAttribute("aria-hidden", "true");
      art.append(element("div", undefined, `shape ${product.art}`));
      const details = element("div", undefined, "product-details");
      const buy = element("button", "Order", "secondary");
      buy.disabled = product.stock === 0;
      buy.setAttribute("aria-label", `Order ${product.name}`);
      buy.addEventListener("click", () => {
        document.querySelector("#assistant").scrollIntoView({block: "start", behavior: "smooth"});
        void send(`/buy ${product.sku} 1`, `Order one ${product.name}`);
      });
      const pricing = element("div", undefined, "product-footer");
      pricing.append(element("strong", currency(product.cents)), buy);
      details.append(element("p", `${product.category} · ${product.stock ? `${product.stock} in stock` : "Sold out"}`, "small"),
        element("h3", product.name), element("p", product.description), pricing);
      card.append(art, details);
      return card;
    }));
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
