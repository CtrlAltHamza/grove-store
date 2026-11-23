import {api, currency, element} from "./shared.js";

const form = document.querySelector("#unlock");
const status = document.querySelector("#status");
const overview = document.querySelector("#overview");
const tokenInput = document.querySelector("#token");

function rows(target, data) {
  document.querySelector(target).replaceChildren(...data.map(values => {
    const row = element("tr");
    row.append(...values.map(value => element("td", value)));
    return row;
  }));
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  status.textContent = "Loading dashboard…";
  try {
    const data = await api("/admin/overview", {headers: {Authorization: `Bearer ${tokenInput.value}`}});
    document.querySelector("#count").textContent = data.summary.order_count;
    document.querySelector("#total").textContent = currency(data.summary.order_total);
    document.querySelector("#stock").textContent = data.products.reduce((sum, product) => sum + product.stock, 0);
    rows("#inventory", data.products.map(product => [product.name, product.sku, currency(product.cents), product.stock]));
    rows("#orders", data.orders.map(order => [
      order.id.slice(0, 8).toUpperCase(), order.name, order.quantity, currency(order.total_cents),
      new Date(order.created_at.replace(" ", "T") + (/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(order.created_at) ? "Z" : "")).toLocaleString()
    ]));
    document.querySelector("#empty-orders").hidden = data.orders.length > 0;
    document.querySelector("#orders-table").hidden = data.orders.length === 0;
    overview.hidden = false;
    status.textContent = "";
    tokenInput.value = "";
  } catch (error) {
    overview.hidden = true;
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
