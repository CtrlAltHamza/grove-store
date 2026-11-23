import {api, currency, element} from "./shared.js";

const form = document.querySelector("#unlock");
const status = document.querySelector("#status");
const overview = document.querySelector("#overview");
const tokenInput = document.querySelector("#token");
const upload = document.querySelector("#catalog-upload");
const fileInput = document.querySelector("#catalog-file");
const preview = document.querySelector("#catalog-preview");
const importStatus = document.querySelector("#import-status");
const previewButton = upload.querySelector("button");
const saveButton = document.querySelector("#save-catalog");
let token = "";
let previewedFile = null;

function rows(target, data) {
  document.querySelector(target).replaceChildren(...data.map(values => {
    const row = element("tr");
    row.append(...values.map(value => element("td", value)));
    return row;
  }));
}

async function loadOverview() {
    const data = await api("/admin/overview", {headers: {Authorization: `Bearer ${token}`}});
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
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  resetPreview();
  const button = form.querySelector("button");
  button.disabled = true;
  status.textContent = "Loading dashboard…";
  token = tokenInput.value;
  try {
    await loadOverview();
    status.textContent = "";
    tokenInput.value = "";
  } catch (error) {
    token = "";
    overview.hidden = true;
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

function resetPreview() {
  previewedFile = null;
  preview.hidden = true;
  importStatus.textContent = "";
}

async function catalogRequest(action, file) {
  return api(`/admin/catalog/${action}`, {
    method: "POST",
    headers: {Authorization: `Bearer ${token}`, "Content-Type": "text/csv"},
    body: file
  });
}

function setUploadBusy(busy) {
  fileInput.disabled = busy;
  previewButton.disabled = busy;
  saveButton.disabled = busy;
  form.querySelector("button").disabled = busy;
}

fileInput.addEventListener("change", resetPreview);
upload.addEventListener("submit", async event => {
  event.preventDefault();
  resetPreview();
  const file = fileInput.files[0];
  if (!file) return;
  setUploadBusy(true);
  importStatus.textContent = "Checking catalog…";
  try {
    const {products} = await catalogRequest("preview", file);
    rows("#preview-rows", products.map(p => [p.sku, p.name, p.category, currency(p.cents), p.stock]));
    previewedFile = file;
    preview.hidden = false;
    importStatus.textContent = `${products.length} valid product rows. Review them before applying.`;
  } catch (error) {
    importStatus.textContent = error.message;
  } finally {
    setUploadBusy(false);
  }
});

saveButton.addEventListener("click", async () => {
  if (!previewedFile) return;
  setUploadBusy(true);
  importStatus.textContent = "Applying catalog…";
  try {
    const {saved} = await catalogRequest("import", previewedFile);
    fileInput.value = "";
    resetPreview();
    importStatus.textContent = `Saved ${saved} product rows.`;
    try {
      await loadOverview();
    } catch {
      importStatus.textContent += " Reload the dashboard to see the changes.";
    }
  } catch (error) {
    importStatus.textContent = error.message;
  } finally {
    setUploadBusy(false);
  }
});
