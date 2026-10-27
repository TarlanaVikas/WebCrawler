import { h, nextId } from "../dom.js";

/** Labelled range slider with a live value readout. */
export function rangeField({ label, hint, min, max, step = 1, value, format = (v) => String(v), name }) {
  const id = nextId("range");
  const readout = h("output", { class: "range-value", for: id }, format(value));
  const input = h("input", { id, name, type: "range", min, max, step, value, "aria-describedby": `${id}-hint` });
  const paint = () => {
    const pct = ((Number(input.value) - Number(min)) / (Number(max) - Number(min) || 1)) * 100;
    input.style.setProperty("--fill", `${pct}%`);
    readout.textContent = format(Number(input.value));
  };
  input.addEventListener("input", paint);
  input.value = value;
  paint();
  const el = h("div", { class: "range" },
    h("div", { class: "range-head" }, h("label", { class: "label", for: id }, label), readout),
    input,
    h("span", { class: "hint", id: `${id}-hint` }, hint));
  return { el, input, set(v) { input.value = v; paint(); } };
}

/** Switch built on a real checkbox (role="switch") so it works with keyboard and screen readers. */
export function switchField({ label, hint, checked = false, name }) {
  const id = nextId("switch");
  const input = h("input", { id, name, type: "checkbox", role: "switch", checked, "aria-describedby": `${id}-hint` });
  const el = h("label", { class: "switch", for: id },
    input, h("span", { class: "switch-track" }),
    h("span", { class: "switch-copy" }, h("strong", null, label), h("span", { id: `${id}-hint` }, hint)));
  return { el, input };
}
