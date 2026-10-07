const productsElement = document.querySelector("#products");
const messageElement = document.querySelector("#message");
const refreshButton = document.querySelector("#refresh");
const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `Request failed (${response.status})`);
  }
  return data;
}

async function loadProducts() {
  const products = await api("/products");
  productsElement.replaceChildren();
  for (const product of products) {
    const card = document.createElement("article");
    const title = document.createElement("h2");
    title.textContent = product.name;
    const details = document.createElement("p");
    details.textContent = `${currency.format(product.price)} · Stock: ${product.stock}`;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Order";
    button.disabled = product.stock === 0;
    button.addEventListener("click", async () => {
      button.disabled = true;
      let confirmed = false;
      try {
        await api("/orders", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_id: product.id, quantity: 1 }),
        });
        confirmed = true;
        messageElement.textContent = `Order confirmed: 1 ${product.name}.`;
        await loadProducts();
      } catch (error) {
        messageElement.textContent = confirmed
          ? `Order confirmed, but stock refresh failed: ${error.message}`
          : `Order failed: ${error.message}`;
      } finally {
        button.disabled = product.stock === 0;
      }
    });
    card.append(title, details, button);
    productsElement.append(card);
  }
}

async function refresh() {
  refreshButton.disabled = true;
  try {
    await loadProducts();
    messageElement.textContent = "Choose a product to order one item.";
  } catch (error) {
    messageElement.textContent = `Could not load products: ${error.message}`;
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", refresh);
refresh();
