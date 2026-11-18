function isSelfUser(address, viewer) {
  return Boolean(address && viewer?.ip && address === viewer.ip);
}

function userLabel(name, address, viewer, translate) {
  const display = name || address || translate("chatUser");
  return isSelfUser(address, viewer) ? translate("selfUser", { name: display }) : display;
}

function chatUserIdentity(message, viewer, users, translate) {
  const address = message.user_ip;
  const own = message.sender === "user" && isSelfUser(address, viewer);
  const knownName = message.user_name && message.user_name !== address ? message.user_name : null;
  const liveName = address ? users.find((user) => user.ip === address)?.name : null;
  const name = knownName || liveName || (own ? viewer.name : null) || message.user_name || address || translate("chatUser");
  return { own, label: userLabel(name, message.sender === "user" ? address : null, viewer, translate) };
}
