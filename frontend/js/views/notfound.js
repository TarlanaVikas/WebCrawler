import { h, icon } from "../dom.js";

export function mount(container) {
  document.title = "Page not found · Web Crawler";
  container.append(h("div", { class: "empty" },
    h("div", { class: "empty-icon" }, icon("network")),
    h("h1", { class: "empty-title", "data-autofocus": true }, "There is nothing at this address"),
    h("p", null, "The link may be mistyped or may point to a crawl that no longer exists."),
    h("a", { class: "btn btn-secondary", href: "#/" }, "Back to the crawler")));
}
