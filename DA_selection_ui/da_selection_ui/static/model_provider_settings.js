(() => {
  "use strict";
  const root = document.getElementById("model-provider-settings");
  if (!root) return;

  const input = document.getElementById("anthropic-api-key");
  const status = document.getElementById("anthropic-credential-status");
  const message = document.getElementById("anthropic-credential-message");
  const requirement = document.getElementById("anthropic-credential-requirement");
  const storage = document.getElementById("anthropic-credential-storage");
  const save = document.getElementById("save-anthropic-key");
  const replace = document.getElementById("replace-anthropic-key");
  const remove = document.getElementById("remove-anthropic-key");
  let configured = false;
  let pending = false;

  const readJson = async (response) => {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.toLowerCase().includes("application/json")) {
      throw new Error("The local DiscoveryAgent server returned an unexpected response. Restart the frontend server and reload Settings.");
    }
    try {
      return await response.json();
    } catch (_error) {
      throw new Error("The local DiscoveryAgent server returned an invalid response. Restart the frontend server and reload Settings.");
    }
  };
  const setMessage = (text, kind = "") => {
    message.textContent = text;
    message.className = `credential-message${kind ? ` is-${kind}` : ""}`;
  };
  const render = (isConfigured) => {
    configured = isConfigured;
    status.textContent = configured ? "Configured" : "Not configured";
    status.className = `credential-status ${configured ? "is-configured" : "is-missing"}`;
    requirement.hidden = configured;
    storage.textContent = configured
      ? "Stored securely in this computer’s credential manager."
      : "Stored securely in this computer’s credential manager. The key is never displayed or written to project files.";
    input.hidden = configured;
    save.hidden = configured;
    replace.hidden = !configured;
    remove.hidden = !configured;
    save.textContent = "Save key";
    save.disabled = pending || !input.value.trim();
  };
  const setPending = (value) => {
    pending = value;
    input.disabled = value;
    save.disabled = value || !input.value.trim();
    replace.disabled = value;
    remove.disabled = value;
  };

  fetch("/api/settings/model-provider/credentials/anthropic")
    .then(async (response) => {
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || "Could not check secure credential storage.");
      render(Boolean(data.configured));
    })
    .catch((error) => {
      render(false);
      setMessage(error.message, "error");
    });

  input.addEventListener("input", () => {
    save.disabled = pending || !input.value.trim();
  });
  replace.addEventListener("click", () => {
    input.value = "";
    input.hidden = false;
    save.hidden = false;
    save.textContent = "Save replacement";
    save.disabled = true;
    setMessage("");
    input.focus();
  });
  save.addEventListener("click", async () => {
    if (pending || !input.value.trim()) return;
    const replacing = configured;
    setPending(true);
    setMessage("");
    try {
      const response = await fetch("/api/settings/model-provider/credentials/anthropic", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: input.value.trim() })
      });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || "Could not save the API key securely.");
      input.value = "";
      render(true);
      setMessage(replacing ? "Anthropic API key replaced securely." : "Anthropic API key saved securely.", "success");
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      setPending(false);
    }
  });
  remove.addEventListener("click", async () => {
    if (pending) return;
    setPending(true);
    setMessage("");
    try {
      const response = await fetch("/api/settings/model-provider/credentials/anthropic", { method: "DELETE" });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || "Could not remove the API key.");
      input.value = "";
      render(false);
      setMessage("Anthropic API key removed from secure storage.", "success");
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      setPending(false);
    }
  });
})();
