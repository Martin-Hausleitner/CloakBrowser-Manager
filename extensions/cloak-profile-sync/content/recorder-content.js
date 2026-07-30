const INSTALLED_FLAG = "__cloakSecureActionRecorderInstalled";

if (!globalThis[INSTALLED_FLAG]) {
  globalThis[INSTALLED_FLAG] = true;
  document.addEventListener("click", recordClick, true);
  document.addEventListener("change", recordInput, true);
}

function recordClick(event) {
  const target = event.target?.closest?.("a,button,input,select,textarea,[role='button'],[tabindex]");
  if (!target) return;
  chrome.runtime.sendMessage({
    type: "RECORDER_EVENT",
    event: {
      type: "click",
      url: location.href,
      selector: selectorFor(target),
      text: visibleText(target),
      button: event.button,
      at: Date.now(),
    },
  });
}

function recordInput(event) {
  const target = event.target;
  if (!target || !isInputLike(target)) return;
  const field = fieldMetadata(target);
  chrome.runtime.sendMessage({
    type: "RECORDER_EVENT",
    event: {
      type: "input",
      url: location.href,
      selector: selectorFor(target),
      field,
      sensitive: isSensitiveField(field),
      valueLength: String(target.value || "").length,
      at: Date.now(),
    },
  });
}

function isInputLike(target) {
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

function fieldMetadata(target) {
  return {
    name: target.getAttribute("name") || "",
    id: target.id || "",
    type: target.getAttribute("type") || target.tagName.toLowerCase(),
    autocomplete: target.getAttribute("autocomplete") || "",
    ariaLabel: target.getAttribute("aria-label") || "",
    placeholder: target.getAttribute("placeholder") || "",
  };
}

function isSensitiveField(field) {
  return /(pass(word)?|pwd|secret|token|bearer|session|cookie|otp|totp|mfa|2fa|passkey|credential|cvv|cvc|card|cc-|cc_|credit|iban|routing|account)/i.test(
    Object.values(field).join(" "),
  );
}

function selectorFor(element) {
  if (element.id) return `#${cssEscape(element.id)}`;
  const name = element.getAttribute("name");
  if (name) return `${element.tagName.toLowerCase()}[name="${cssAttr(name)}"]`;
  const role = element.getAttribute("role");
  if (role) return `${element.tagName.toLowerCase()}[role="${cssAttr(role)}"]`;
  const path = [];
  let node = element;
  while (node && node.nodeType === Node.ELEMENT_NODE && path.length < 4) {
    let part = node.tagName.toLowerCase();
    const parent = node.parentElement;
    if (parent) {
      const siblings = [...parent.children].filter((child) => child.tagName === node.tagName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
    }
    path.unshift(part);
    node = parent;
  }
  return path.join(" > ");
}

function visibleText(element) {
  const field = fieldMetadata(element);
  if (isSensitiveField(field)) return "";
  const value =
    element.getAttribute("aria-label") ||
    element.innerText ||
    element.getAttribute("title") ||
    element.getAttribute("placeholder") ||
    "";
  return String(value).replace(/\s+/g, " ").trim().slice(0, 80);
}

function cssEscape(value) {
  if (globalThis.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function cssAttr(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}
