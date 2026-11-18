class RecipientSelection {
  constructor(storage) {
    this.storage = storage;
    this.id = "all";
    this.name = "";
    try {
      const saved = JSON.parse(storage.getItem("relay-recipient"));
      if (saved && typeof saved.id === "string" && /^[a-zA-Z0-9_-]{1,32}$/.test(saved.id)) {
        this.id = saved.id;
        this.name = typeof saved.name === "string" ? saved.name.slice(0, 32) : saved.id;
      }
    } catch {
      // Missing or unavailable browser storage keeps the Everyone default.
    }
  }

  choose(id, participants) {
    const agent = participants.find((item) => item.id === id);
    if (id !== "all" && !agent) return false;
    this.id = id;
    this.name = agent?.name || "";
    try {
      this.storage.setItem("relay-recipient", JSON.stringify({ id: this.id, name: this.name }));
    } catch {
      // The explicit selection still remains active for this page.
    }
    return true;
  }

  available(participants) {
    return this.id === "all" ? participants.length > 0 : participants.some((agent) => agent.id === this.id);
  }

  options(participants, translate) {
    const options = [{ id: "all", name: translate("all") }, ...participants.map(({ id, name }) => ({ id, name }))];
    if (this.id !== "all" && !this.available(participants)) {
      options.push({ id: this.id, name: translate("recipientUnavailable", { name: this.name || this.id }), disabled: true });
    }
    return options;
  }
}
