/** Native selects own the value; this layer adds an accessible, styled popup. */
type SelectControl = {
  select: HTMLSelectElement;
  wrapper: HTMLSpanElement;
  trigger: HTMLButtonElement;
  menu: HTMLDivElement;
  sync: () => void;
  highlight: (index: number) => void;
  activeIndex: number;
};

const controls = new Map<HTMLSelectElement, SelectControl>();
let active: SelectControl | null = null;
let initialized = false;
let nextId = 0;

export function closeActiveSelect(): boolean {
  if (!active) return false;
  active.menu.hidden = true;
  active.trigger.setAttribute("aria-expanded", "false");
  active.trigger.removeAttribute("aria-activedescendant");
  active.wrapper.classList.remove("is-open");
  active = null;
  return true;
}

function placeMenu(control: SelectControl): void {
  const rect = control.trigger.getBoundingClientRect();
  const unit = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const margin = unit * 0.5;
  const gap = unit * 0.25;
  const below = window.innerHeight - rect.bottom - margin - gap;
  const above = rect.top - margin - gap;
  const upwards = below < Math.min(control.menu.scrollHeight, unit * 12) && above > below;
  const height = Math.max(0, Math.min(unit * 18, upwards ? above : below));
  const width = Math.min(rect.width, window.innerWidth - margin * 2);
  control.menu.style.position = "fixed";
  control.menu.style.width = `${width / unit}rem`;
  control.menu.style.maxHeight = `${height / unit}rem`;
  control.menu.style.left = `${Math.max(margin, Math.min(rect.left, window.innerWidth - margin - width)) / unit}rem`;
  control.menu.style.top = upwards ? "auto" : `${(rect.bottom + gap) / unit}rem`;
  control.menu.style.bottom = upwards ? `${(window.innerHeight - rect.top + gap) / unit}rem` : "auto";
}

function unavailable(option: HTMLOptionElement): boolean {
  return option.disabled || option.hidden || (option.parentElement instanceof HTMLOptGroupElement && (option.parentElement.disabled || option.parentElement.hidden));
}

function extractLabelText(labels: HTMLLabelElement[], select: HTMLSelectElement): string {
  const parts: string[] = [];
  for (const label of labels) {
    const textNodes = Array.from(label.childNodes)
      .filter(node => node !== select && !(node instanceof HTMLElement && node.contains(select)))
      .map(node => node.textContent?.trim() || "")
      .filter(Boolean);
    if (textNodes.length > 0) parts.push(textNodes.join(" "));
  }
  return parts.join(" ");
}

function enhance(select: HTMLSelectElement): void {
  if (controls.has(select) || select.multiple || select.size > 1) return;
  const id = `acg-select-${++nextId}`;
  const wrapper = document.createElement("span");
  wrapper.className = "acg-select";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.id = `${id}-trigger`;
  trigger.className = "acg-select-trigger";
  trigger.setAttribute("role", "combobox");
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", `${id}-menu`);
  const value = document.createElement("span");
  value.className = "acg-select-value";
  const arrow = document.createElement("span");
  arrow.className = "acg-select-arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "⌄";
  trigger.append(value, arrow);
  const menu = document.createElement("div");
  menu.id = `${id}-menu`;
  menu.className = "acg-select-menu";
  menu.setAttribute("role", "listbox");
  menu.hidden = true;

  // Capture labels before inserting a second labelable control into a wrapping label.
  const labels = Array.from(select.labels || []);
  const labelText = extractLabelText(labels, select);
  for (const label of labels) {
    label.addEventListener("click", event => {
      if (event.target === label || (event.target instanceof Node && !wrapper.contains(event.target))) {
        event.preventDefault();
        trigger.focus();
      }
    });
  }
  select.before(wrapper);
  wrapper.append(select, trigger);
  select.hidden = true;
  document.body.append(menu);

  let signature = "";
  let previousSelection = select.selectedIndex;
  let query = "";
  let typedAt = 0;
  const control: SelectControl = {
    select, wrapper, trigger, menu, activeIndex: select.selectedIndex,
    sync() {
      trigger.disabled = select.matches(":disabled");
      wrapper.classList.toggle("is-disabled", trigger.disabled);
      const labelledBy = select.getAttribute("aria-labelledby");
      const name = select.getAttribute("aria-label") || labelText || select.title || select.name || "選擇項目";
      for (const element of [trigger, menu]) {
        if (labelledBy) {
          element.setAttribute("aria-labelledby", labelledBy);
          element.removeAttribute("aria-label");
        } else {
          element.setAttribute("aria-label", name);
          element.removeAttribute("aria-labelledby");
        }
      }
      for (const attribute of ["aria-describedby", "aria-invalid", "aria-busy", "aria-required"]) {
        const attributeValue = select.getAttribute(attribute);
        if (attributeValue !== null) trigger.setAttribute(attribute, attributeValue);
        else trigger.removeAttribute(attribute);
      }
      if (select.required) trigger.setAttribute("aria-required", "true");
      value.textContent = select.selectedOptions[0]?.label || "選擇項目";
      const options = Array.from(select.options);
      const nextSignature = JSON.stringify(options.map(option => [option.label, option.value, unavailable(option), option.hidden]));
      const optionsChanged = signature !== nextSignature;
      const selectionChanged = previousSelection !== select.selectedIndex;
      previousSelection = select.selectedIndex;
      if (optionsChanged) {
        signature = nextSignature;
        menu.replaceChildren(...options.map((option, index) => {
          const item = document.createElement("div");
          item.className = "acg-select-option";
          item.id = `${id}-option-${index}`;
          item.setAttribute("role", "option");
          item.setAttribute("aria-disabled", String(unavailable(option)));
          item.hidden = option.hidden;
          item.textContent = option.label;
          item.addEventListener("pointermove", () => {
            if (!unavailable(option)) control.highlight(index);
          });
          item.addEventListener("mousedown", event => event.preventDefault());
          item.addEventListener("click", () => choose(index));
          return item;
        }));
      }
      Array.from(menu.children).forEach((item, index) => {
        item.setAttribute("aria-selected", String(index === select.selectedIndex));
      });
      if (active !== control) return;
      if (trigger.disabled || !trigger.getClientRects().length) {
        closeActiveSelect();
        return;
      }
      if (selectionChanged) control.activeIndex = select.selectedIndex;
      const activeOption = options[control.activeIndex];
      if (!activeOption || unavailable(activeOption)) {
        control.highlight(options.findIndex(option => !unavailable(option)));
      } else if (optionsChanged || selectionChanged) {
        control.highlight(control.activeIndex);
      }
      placeMenu(control);
    },
    highlight(index) {
      control.activeIndex = index;
      Array.from(menu.children).forEach((item, itemIndex) => item.classList.toggle("is-active", index === itemIndex));
      const item = menu.children[index] as HTMLElement | undefined;
      if (item) {
        trigger.setAttribute("aria-activedescendant", item.id);
        item.scrollIntoView({ block: "nearest" });
      } else trigger.removeAttribute("aria-activedescendant");
    },
  };
  controls.set(select, control);

  function choose(index: number): void {
    const option = select.options[index];
    if (trigger.disabled || !option || unavailable(option)) return;
    const changed = select.selectedIndex !== index;
    select.selectedIndex = index;
    if (changed) {
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    control.sync();
    closeActiveSelect();
    trigger.focus();
  }

  function open(): void {
    control.sync();
    if (trigger.disabled) return;
    closeActiveSelect();
    active = control;
    query = "";
    menu.hidden = false;
    wrapper.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    placeMenu(control);
    const currentOption = select.options[select.selectedIndex];
    const initialIndex = currentOption && !unavailable(currentOption)
      ? select.selectedIndex
      : Array.from(select.options).findIndex(option => !unavailable(option));
    control.highlight(initialIndex);
  }

  trigger.addEventListener("click", () => active === control ? closeActiveSelect() : open());
  trigger.addEventListener("keydown", event => {
    if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
    const navigates = ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key);
    if (navigates) {
      event.preventDefault();
      if (active !== control) open();
      const available = Array.from(select.options).map((option, index) => unavailable(option) ? -1 : index).filter(index => index >= 0);
      const cursor = available.indexOf(control.activeIndex);
      let target: number;
      if (event.key === "Home") {
        target = 0;
      } else if (event.key === "End") {
        target = available.length - 1;
      } else {
        const step = event.key === "ArrowDown" ? 1 : -1;
        target = Math.max(0, Math.min(available.length - 1, cursor + step));
      }
      control.highlight(available[target] ?? -1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (active === control) choose(control.activeIndex);
      else open();
    } else if (event.key.length === 1) {
      event.preventDefault();
      if (active !== control) open();
      const now = Date.now();
      query = now - typedAt > 700 ? event.key : query + event.key;
      typedAt = now;
      const term = Array.from(query).every(letter => letter === query[0]) ? query[0] : query;
      const options = Array.from(select.options);
      const start = term.length === 1 ? control.activeIndex + 1 : control.activeIndex;
      for (let offset = 0; offset < options.length; offset++) {
        const index = (start + offset + options.length) % options.length;
        if (!unavailable(options[index]) && options[index].label.toLocaleLowerCase().startsWith(term.toLocaleLowerCase())) {
          control.highlight(index);
          break;
        }
      }
    }
  });
  select.addEventListener("input", control.sync);
  select.addEventListener("change", () => {
    control.sync();
    if (active === control) control.highlight(select.selectedIndex);
  });
  select.form?.addEventListener("reset", () => queueMicrotask(control.sync));
  new MutationObserver(control.sync).observe(select, { attributes: true, childList: true, subtree: true, characterData: true });
  // Programmatic value assignments do not emit change or mutation events.
  for (const key of ["value", "selectedIndex"] as const) {
    const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, key)!;
    Object.defineProperty(select, key, {
      configurable: true,
      get() { return descriptor.get!.call(this); },
      set(next: string | number) { descriptor.set!.call(this, next); control.sync(); },
    });
  }
  control.sync();
}

export function initializeSelects(): void {
  document.querySelectorAll<HTMLSelectElement>("select").forEach(enhance);
  if (initialized) return;
  initialized = true;
  document.addEventListener("pointerdown", event => {
    if (active && event.target instanceof Node && !active.wrapper.contains(event.target) && !active.menu.contains(event.target)) closeActiveSelect();
  });
  document.addEventListener("focusin", event => {
    if (active && event.target instanceof Node && !active.wrapper.contains(event.target) && !active.menu.contains(event.target)) closeActiveSelect();
  });
  document.addEventListener("keydown", event => {
    if (!active || event.isComposing) return;
    if (event.key === "Escape") {
      const trigger = active.trigger;
      closeActiveSelect();
      trigger.focus();
      event.preventDefault();
      event.stopPropagation();
    } else if (event.key === "Tab") closeActiveSelect();
  }, true);
  window.addEventListener("resize", () => { if (active) placeMenu(active); });
  document.addEventListener("scroll", event => {
    if (active && event.target !== active.menu) placeMenu(active);
  }, true);
  new MutationObserver(records => {
    const hasAddedSelects = records.some(
      record => record.type === "childList" && Array.from(record.addedNodes).some(
        node => node instanceof Element && (node.matches("select") || Boolean(node.querySelector("select"))),
      ),
    );
    if (hasAddedSelects) initializeSelects();

    for (const [select, control] of controls) {
      if (!select.isConnected) {
        if (active === control) closeActiveSelect();
        control.menu.remove();
        controls.delete(select);
      } else {
        const hasAttributeChange = records.some(
          record => record.type === "attributes" && record.target instanceof Element && record.target.contains(select),
        );
        if (hasAttributeChange) control.sync();
      }
    }
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "hidden"] });
}
