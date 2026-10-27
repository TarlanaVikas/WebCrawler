import { h } from "../dom.js";

/** Accessible confirmation built on <dialog>. Resolves true when confirmed. */
export function confirmDialog({ title, message, confirmLabel = "Confirm", tone = "danger" }) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { class: "dialog", "aria-labelledby": "confirm-title" },
      h("h2", { id: "confirm-title" }, title),
      h("p", null, message),
      h("div", { class: "dialog-actions" },
        h("button", { class: "btn btn-secondary", type: "button", onclick: () => dialog.close("cancel") }, "Cancel"),
        h("button", { class: `btn ${tone === "danger" ? "btn-danger" : "btn-primary"}`, type: "button", onclick: () => dialog.close("ok") }, confirmLabel)));
    dialog.addEventListener("close", () => { resolve(dialog.returnValue === "ok"); dialog.remove(); });
    document.body.append(dialog);
    dialog.showModal();
    dialog.querySelector(".btn-secondary").focus();
  });
}
